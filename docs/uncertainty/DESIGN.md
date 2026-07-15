# Z Uncertainty Tree — Design Document

**Status:** Implemented on `main`  
**Code root:** `aider/z/uncertainty/`  
**User-facing overview:** [README.md](./README.md)

This document describes how the Uncertainty Tree works end-to-end: what it is for, how it is wired into the agent loop, how each detector decides that generated code is uncertain, and how verification + the commit gate turn findings into behavior.

---

## 1. Thesis

Coding agents can produce diffs that look correct and still be wrong. Model self-rated confidence (“I’m 87% sure”) is not evidence.

Z’s Uncertainty Tree answers a different question:

> **What would a careful human developer worry about in this change — and can we check those worries against the repo and the session?**

Design rules:

| Rule | Meaning |
|------|---------|
| Signals over vibes | Tiers come from tests, AST, paths, evidence — not model percentages |
| Risk ≠ confidence | “How bad if wrong” is independent of “how much evidence we have” |
| Human-worry nodes | Titles read like a senior engineer’s checklist, not detector IDs |
| Gate drives behavior | High findings block commit; Medium needs explicit ack |
| Quality over quantity | Cap nodes; suppress greenfield / scaffold noise |
| LLM writes code; detectors inspect it | Detection is mostly deterministic; the model is not the primary judge of its own uncertainty |

---

## 2. Core concepts

### 2.1 Uncertainty node

An `UncertaintyNode` (`schema.py`) is one inspectable worry:

- **type** — human-worry label (e.g. Untested Path, Edge Case Blind Spot)
- **risk_tier** — Low / Medium / High (how bad if wrong)
- **confidence_tier** — Low / Medium / High (how much checkable evidence we have)
- **summary / explanation / why_uncertain / what_could_go_wrong**
- **files_affected / symbols_affected**
- **suggested_fix / suggested_tests / suggested_prompt**
- **status** — Open, In Progress, Resolved, Ignored, Needs Human Review, Blocked
- **signals** — detector metadata (verification_blocked, requirement_status, edge_source, …)

### 2.2 Risk vs confidence

Derived in `risk.py` from a `DetectionSignals` bag of facts:

**Risk** rises with: high-stakes paths (auth/payment/…), migrations, blast radius, failing tests, requirement gaps.

**Confidence** rises with: relevant tests exist + passed, live-verified APIs, matched tested patterns. Falls with: no tests, unverified APIs/MCP, conflicting patterns, missing secrets.

A change can be **high risk + low confidence** (stop) or **low risk + high confidence** (lighter review).

### 2.3 Gate-effective tier

Stored `risk_tier` and **gate tier** can differ. Example: a Requirement Gap may be stored Medium but `_effective_gate_tier()` promotes Not Addressed **product** gaps to High for the commit gate. UI and gate messages use the **effective** tier so labels stay consistent.

Process/decision requirement gaps are forced **Low** for the gate so “use uncertainty” never invents product features or hard-blocks.

---

## 3. Pipeline (when it runs)

```text
user message
    │
    ▼
begin_task → decompose checklist (product/process/verification/decision)
    │
    ▼
agent edits files
    │
    ├─ (optional mid-turn) analyze_edits after settle
    │
    ▼
prepare_commit (verify-before-commit gate)
    │
    ├─ 1. verify_edits → VerificationRecord + VerifyState
    ├─ 2. analyze_edits → detectors + checklist rescore
    ├─ 3. auto-act (OFF unless Z_UNCERTAINTY_AUTO_ACT=1)
    └─ 4. classify High/Medium → block / ack / allow
            │
            ▼
        commit only if allowed
```

### Wire-in points (`base_coder.py`)

| Hook | Role |
|------|------|
| `attach_engine_to_coder` | Session start — store under `~/.z/uncertainty/` |
| `_maybe_begin_uncertainty_task` | New user turn → checklist |
| `_run_uncertainty_analysis` | After edits settle → detectors (also ingest reply for discussed text / edge list / diff) |
| `prepare_commit` | Before auto-commit → verify + full analyze + gate |

Dirty-commits are **held** while verify reflect/recovery is pending so a broken first draft is not committed before fixes.

---

## 4. How uncertainty is detected in agent-generated code

Detection is **not** “ask the LLM if it’s unsure.” After the agent writes code, Z reads the **changed files**, optional **diff**, **test suite results**, and **session context**, then runs specialized detectors.

### 4.1 Inputs to `analyze_edits`

| Input | Source |
|-------|--------|
| `files_changed` | Paths the agent edited |
| `file_contents` | Final on-disk text of those files |
| `symbols` | Extracted / inferred from contents |
| `tests_passed` | Last verification outcome |
| `discussed_text` | Agent reply (for “was this branch discussed?”) |
| `last_diff` | Git diff (scopes structural edge detection to new lines) |
| `execution_log` | Accumulated session facts for process requirements |
| `checklist` | Decomposed user request |
| Repo maturity | greenfield / young / mature (`context.py`) |

### 4.2 Detector catalog

Each detector returns zero or more nodes. Engine caps/prioritizes (~8) and dedupes.

#### Untested Path — `detect_missing_or_failing_tests`

**Signal:** Relevant test files missing, suite discovered zero tests, or tests failed.

**How:** `find_relevant_tests` looks for co-located / `tests/test_<stem>.py` / symbol-linked tests. Combined with `VerificationRecord` from the gate.

**Gate:** Verification failures become High via `verification_blocked` signal.

#### Edge Case Blind Spot — `detect_edge_cases` + `edges.py`

**Signal:** Control-flow branches that look like weird-data paths and were not discussed/tested.

**How (primary — structural):**

1. Parse changed files (Python **AST**; regex fallback elsewhere).
2. Extract edge-ish branches: `else` / `elif` / `except` / `match` case / `None` checks / empty checks / falsy guards / bound checks.
3. Optionally restrict to **changed lines** from the unified diff.
4. Drop branches that appear in the agent reply (`discussed_text`) or whose enclosing symbol appears in relevant test text.
5. Cap (~4) undiscussed branches → nodes with `edge_source=structural`.

**How (supplement — model list):** Parsed “edge cases considered but not fully handled” from the agent reply. Empty model list **cannot** silence structural detection.

#### Unverified Assumption — `detect_api_assumptions`

**Signal:** External API / MCP tool used or assumed this session without live verification.

**How:** Session sets `assumed_apis` / `mcp_unverifiable` from imports and tool use; `live_verified_apis` clears those that were actually exercised.

#### High-Stakes Surface / Migration Risk — `detect_high_stakes_and_migration`

**Signal:** Paths/names/keywords for payments, auth, security, webhooks; migration files / schema wording without clear data-impact handling.

**How:** Keyword and path heuristics (`schema.HIGH_STAKES_KEYWORDS`, `path_looks_migration`, file contents). Optional migration impact text from the agent reply.

#### Fragile Logic — `detect_fragile_logic`

**Signal:** Broad `except:`, deep nesting, “hack/temporary”, magic-number conditionals.

**How:** Regex heuristics on changed file text (not full cyclomatic complexity yet). Skips scaffold files.

#### Failure Blind Spot — `detect_failure_blind_spots`

**Signal:** I/O / HTTP / DB / subprocess calls without nearby try/except / timeout / retry / rollback signals.

**How:** Regex for I/O call sites vs error-handling tokens in the same file.

#### Integration Ripple — `detect_blast_radius` + `count_symbol_references`

**Signal:** Changed symbol referenced widely elsewhere in the repo.

**How:** Text search for the short symbol name across source files; compare count to `blast_radius_threshold` (softened in greenfield). Note: substring count is a known approximation (not full AST/LSP refs).

#### Pattern Misfit / New File — `detect_pattern_issues`

**Signal:** New file with no peer pattern, or conflicting conventions.

**How:** Peer search by stem/suffix. **Suppressed in greenfield**; pattern misfit only when maturity allows.

#### Unverifiable Config — `detect_unverifiable_config`

**Signal:** Env/secret refs (`os.environ`, `STRIPE_*`, …) not present in accessible env.

**How:** Regex extract config refs from changed files; compare to process env keys.

#### TODO / Unclear Comment — `detect_todo_comments`

**Signal:** TODO/FIXME markers near the change.

**How:** Marker scan on file text.

#### Requirement Gap — `detect_requirement_gaps` + `checklist.py`

**Signal:** Checklist item not Fully Addressed after implementation.

**How:**

1. `decompose_request` splits the user ask into items with **kind**:
   - `product` — implement in code (files/symbols/tests)
   - `process` — agent/tooling (“use uncertainty”) → scored from **execution_log**, not source
   - `verification` — from `VerificationRecord.meaningful_pass`
   - `decision` — from user confirms / decisions
2. `bind_evidence` + `rescore_checklist_with_evidence` (optional model JSON rescore).
3. Gaps become nodes; **product** Not Addressed → High for gate; **process/decision** → Low (no product invention).

#### Evidence of Safety — `detect_high_confidence`

**Signal:** Closely matches a tested pattern **and** tests passed.

**How:** Positive / informational only — never blocks commit.

---

## 5. Verification (evidence for confidence + gate)

`verify.py` runs a real suite and builds a `VerificationRecord`:

| Field | Role |
|-------|------|
| `command` | Detected or configured test command |
| `exit_code` | Process exit |
| `tests_discovered` / `tests_passed` / `tests_failed` | Parsed counts |
| `state` | Structured `VerifyState` |
| `smoke_*` | Import smoke and/or **process CLI smoke** (`sys.executable -m <cli> --help`) |

### VerifyState

| State | Meaning |
|-------|---------|
| `NO_TESTS` | Zero discovered / empty suite |
| `TESTS_PASSED` | Discovered > 0 and exit 0 |
| `TESTS_FAILED` | Discovered > 0 but failed |
| `COLLECTION_FAILED` | Collection errors |
| `RUNNER_MISSING` | No test command |
| `TIMED_OUT` / `NOT_RUN` | Runner issues |

**Critical rule:** `2 failed, 7 passed` is `TESTS_FAILED` → **fix reflect**, never “no tests discovered / generate tests.” Branching uses suite discovery, not empty `find_relevant_tests`.

**Runner choice:** pytest only if declared (`pytest.ini`, `conftest.py`, deps). Otherwise dependency-free layouts use:

```bash
<sys.executable> -m unittest discover -s tests -v
```

`meaningful_pass` requires: ran + `TESTS_PASSED` + discovered > 0 + exit 0.

---

## 6. Commit gate (`gate.py`)

| Effective tier | Behavior |
|----------------|----------|
| **High** | Hard block until Resolved (Ignored does **not** clear). Force override logged (`--force-commit` / `Z_FORCE_COMMIT`) |
| **Medium** | Explicit user ack required (`explicit_yes_required` — `--yes` cannot bypass) |
| **Low** | Informational |

Escape hatches: `--no-verify-commit-gate`, `Z_SKIP_VERIFY_GATE=1`.

Reflect loops (bounded):

- `NO_TESTS` → generate tests once  
- `TESTS_FAILED` → fix tests (limited attempts)  
- Sets `_z_gate_hold_dirty` so dirty-commit does not ship the broken WIP mid-recovery  

---

## 7. Auto-act (deliberately constrained)

`auto_act.py` can turn High findings into a reflect prompt. **Default: OFF.**

Enable only with `Z_UNCERTAINTY_AUTO_ACT=1`.

Rationale (from evals): unconstrained auto-act invented product commands, lexical “policy tests,” and failure spirals. Safe automatic actions should stay narrow (e.g. re-run an existing test command); scope expansion requires the user.

---

## 8. Session context the detectors share

`SessionContext` accumulates facts the model alone would not reliably report:

- Live vs assumed APIs, MCP unverifiable tools  
- Checklist + confirmation  
- Edge cases listed in the reply (supplement only)  
- Discussed text + execution log (process evidence)  
- Last diff + last verification  
- Pattern search cache  
- Repo maturity for noise control  

---

## 9. Persistence, UI, telemetry

| Layer | Location |
|-------|----------|
| Local nodes | `~/.z/uncertainty/<repo>.json` |
| Disposition stats | `~/.z/uncertainty/outcomes.json` |
| Browse | `/uncertainties` (risk / file / session) |
| Stats | `/uncertainties stats` or `z uncertainty stats` |
| Optional sync | Signed-in → `/v1/uncertainty/*` |

Disposition counters (created / ignored / resolved / force_override / medium_ack) per detector support a thin calibration loop — record + report today; auto-tune thresholds later.

---

## 10. Noise control

- **Greenfield / young repos:** suppress “new file has no pattern”; raise blast-radius threshold  
- **Scaffold files:** README, `__init__.py`, license, … skipped  
- **Node cap:** prioritize and keep a small set per turn  
- **Process requirements:** never High-block; never demand the word “uncertainty” in product source  

---

## 11. What the LLM does vs does not do

| LLM | Detectors / gate |
|-----|------------------|
| Writes application code | Inspects AST, paths, tests, session facts |
| May list edge cases in the reply (supplement) | Structural edges fire even if the list is empty |
| Optional structured checklist rescore JSON | Evidence rules still override empty evidence |
| Does **not** assign risk % | Tiers from `derive_risk_tier` / `derive_confidence_tier` |

---

## 12. Code map

| Module | Responsibility |
|--------|----------------|
| `schema.py` | Node types, tiers, statuses, checklist items |
| `risk.py` | `DetectionSignals`, risk/confidence derivation |
| `engine.py` | Session context, `analyze_edits` orchestration |
| `detectors.py` | All human-worry detectors |
| `edges.py` | Structural edge-case AST/regex extraction |
| `checklist.py` | Decompose, bind evidence, rescore |
| `verify.py` | Suite run, VerifyState, smoke |
| `gate.py` | Verify-before-commit policy |
| `auto_act.py` | Optional bounded reflects (off by default) |
| `context.py` | Maturity / scaffold / prioritization |
| `store.py` | Persist nodes |
| `outcomes.py` | Disposition telemetry |
| `ui.py` / `tree.py` / `actions.py` | Browse and follow-ups |
| `base_coder.py` | Agent loop wire-in |

---

## 13. Environment flags

| Flag | Effect |
|------|--------|
| `Z_SKIP_VERIFY_GATE=1` | Disable verify-before-commit gate |
| `Z_FORCE_COMMIT=1` / `--force-commit` | Logged override of High blockers |
| `Z_UNCERTAINTY_AUTO_ACT=1` | Enable auto-act reflects |
| `Z_NO_DIRTY_COMMIT=1` | Never dirty-commit before edits |
| `Z_BLAST_RADIUS_THRESHOLD` | Reference-count threshold (default 5) |

---

## 14. Known limitations (honest)

| Area | Limitation | Direction |
|------|------------|-----------|
| Blast radius | Substring `text.count` refs | AST/LSP find-references |
| Fragile logic | Regex nesting / except heuristics | Cyclomatic / cognitive complexity on changed functions |
| Outcome loop | Record + report only | Use override rates to raise noisy detectors |
| CLI smoke | `--help` exit check when CLI modules edited | Broader failing-input smoke contracts per project |

---

## 15. One-sentence summary

**After the agent edits code, Z runs checkable detectors (AST, tests, paths, session evidence), builds a human-worry tree with separate risk and confidence, and blocks commit until High issues are resolved and Medium ones are explicitly acknowledged — without trusting the model’s self-rated certainty.**
