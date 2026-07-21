# Deep dive + implementation plan: NI / verify / skills / gate faults

**Status:** planning — **none of F1–F5 are implemented in runtime code**  
**As of:** `main` tip at planning time (`a3e0c1c15` and later)  
**Companion short spec:** [fault-plan-ni-verify-skills-gate.md](./fault-plan-ni-verify-skills-gate.md)  
**This doc:** repo evidence (what exists today), feature design, file-level work, tests, flags, PR slices

---

## TL;DR — why this felt “done” but isn’t

| What shipped | What did **not** ship |
|--------------|------------------------|
| Coding-quality stack (compact skills, explore, plan interview, tool-loop, P2 live) | Non-interactive exit contract |
| Uncertainty tree + verify gate (real tests, High/Medium tiers) | CMake reconfigure before `ctest` |
| Dynamic sanitizer honesty nodes (`tool_missing`) | Hard teeth for missing sanitizers in NI |
| Skill retrieve (Chroma + keyword) + bug_pattern capture under yes-always | Lexical stem fallback + near-dup merge |
| `Z_FORCE_COMMIT` / `Z_SKIP_VERIFY_GATE` as **env escapes** | Printing those escapes on every block; NI-aware override UX |
| Fault **planning** doc on `main` | Any of `Z_NI_*`, `Z_CMAKE_*`, `Z_SANITIZER_POLICY`, `Z_SKILL_NEAR_DUP`, `ANONYMIZED_TELEMETRY` wiring |

**Proof of absence:** a repo-wide search for `Z_NI_REQUIRE_EDITS`, `Z_NI_GATE`, `Z_SANITIZER_POLICY`, `Z_CMAKE_RECONFIGURE`, `Z_SKILL_NEAR_DUP`, `ni_contract`, `cmake_verify`, `near_dup`, and `ANONYMIZED_TELEMETRY` hits **only** the planning markdown — not Python.

---

## Architecture context (what already works)

```text
z / aider.main
  → Coder.run(with_message=…)          # one-shot for --message / --message-file
  → plan gate (auto-approve if io.yes) # base_coder._maybe_require_implementation_plan
  → edit / reflect / explore / tools   # coding-quality stack
  → settle edits
  → prepare_commit (gate.py)           # verify + detectors + High/Medium
  → auto_commit OR block
  → optional skill capture
```

The uncertainty gate **does** fail closed on High when interactive override is refused. The product bugs below are about **contracts, honesty with teeth, retrieve quality, and automation UX** — not “gate doesn’t exist.”

---

## F1 — Non-interactive exit 0 with zero edits

### Observed product failure

`--yes-always --message-file SPEC.md` prints a plan, “Plan approved — proceeding…,” model asks to add files, process exits **0**, no commits / no product files.

### Repo evidence (current code)

| Location | Behavior today |
|----------|----------------|
| `aider/main.py` ~1200–1225 | `--message` / `--message-file`: call `coder.run(...)`, then **`return`** (Python `None` → `sys.exit(None)` → **0**). Only file-not-found / IO errors return `1`. **No check of `aider_edited_files`.** |
| `base_coder._maybe_require_implementation_plan` ~2321–2352 | If `io.yes is True`, skip confirm; print **“Plan approved — proceeding with implementation.”** and inject plan into chat. |
| `io.confirm_ask` ~891–892 | `yes is True` + normal confirm → auto **`y`**. |
| Chat empty + strict edits | New files creatable; model often still emits “please add files” prose instead of SEARCH/REPLACE. |
| One-shot end | No second reflect for “add-files” miss; no NI outcome summary. |

### Feature to implement: `ni-contract`

**Module:** new `aider/z/ni_contract.py`  
**Call sites:** end of `--message` / `--message-file` paths in `aider/main.py`; optional mid-turn hook in `base_coder` when NI + empty edits + “add files” prose detected.

#### Behavior

1. **Classify run mode** from task text / existing intent helpers:
   - `implement` (default for SPEC-driven coding)
   - `ask` / `diagnose` / `review` (non-edit OK if artifact present)
2. **Outcome record** after one-shot:

   ```text
   Run outcome: edited=N verify=<state|n/a> commit=<hash|none|blocked> gate=<ok|blocked|skipped>
   ```

3. **Exit code policy** (`Z_NI_REQUIRE_EDITS=1` default):
   - implement + `len(aider_edited_files)==0` → **exit ≠ 0**
   - implement + edits but gate blocked → **exit ≠ 0** (coordinate with F4)
   - ask/diagnose/review + non-empty assistant artifact → exit 0 allowed
4. **Empty-chat recovery (one reflect, counts toward `max_reflections`):**
   - Detect assistant prose matching add-files / “not in chat” patterns
   - Auto-seed chat from: explore scout candidates, SPEC path mentions, `allowed` globs, basename matches under root
   - Set `reflected_message` to a short “files added — implement now with SEARCH/REPLACE” directive
   - Raise NI default reflections floor to ≥5 when `--message`/`--message-file` used
5. Under `--yes-always`, never treat “please add these files” as successful completion.

#### Acceptance tests (`tests/basic/test_z_ni_contract.py`)

- Fixture: empty CMake/`src` layout + SPEC requiring new `src/foo.c`; stub coder that edits nothing → `evaluate_ni_outcome` returns nonzero + `edited=0`.
- Same with `aider_edited_files={"src/foo.c"}` + green verify stub → exit 0 path.
- Ask-mode task string → zero edits allowed.
- Add-files prose detector + seed helper returns expected paths from a temp tree.

#### Flags

| Env | Default | Meaning |
|-----|---------|---------|
| `Z_NI_REQUIRE_EDITS` | `1` | Nonzero if implement NI run edits nothing |
| `Z_NI_AUTO_SEED` | `1` | Auto-add candidates + one reflect on add-files miss |
| `Z_NI_MIN_REFLECTIONS` | `5` | Floor for NI one-shot reflection budget |

---

## F2 — Verify green on stale CMake / wrong suite

### Observed product failure

Edit `CMakeLists.txt` to add `minilfu_tests`; existing `build/` still only knows `miniregex_tests`; `ctest --test-dir build` reports 100% pass; new target never discovered.

### Repo evidence

| Location | Behavior today |
|----------|----------------|
| `verify.detect_test_command` ~251–252 | If `CMakeLists.txt` **and** `build/` → **`ctest --test-dir build` only** — **no** `cmake -S . -B build`. |
| `VerificationRecord` | Has relevant-test fields; **no** `reconfigured`, `discovered_tests` list from `ctest -N`, or `matched_change_tests`. |
| `find_relevant_tests` | Path/name heuristics; does not prove ctest registered a new target. |
| Repo search | **Zero** `cmake -S` / reconfigure helpers under `aider/`. |

### Feature to implement: `verify-cmake`

**Module:** new `aider/z/uncertainty/cmake_verify.py`  
**Wire into:** `verify_edits` / `detect_test_command` path before ctest runs.

#### Behavior

1. **Build-system dirty set:** edited intersects  
   `CMakeLists.txt`, `CMakeLists.txt.in`, `*.cmake`, optionally root `Makefile` / `meson.build`.
2. When dirty and `Z_CMAKE_RECONFIGURE=1` (default):
   - Run `cmake -S <src> -B <build>` (prefer existing `build/` cache; create if missing).
   - Budget stdout/stderr via existing tool-output budget helpers.
   - On reconfigure failure → `VerifyState` failure / fail closed (not “skip and ctest anyway”).
3. After reconfigure (and always for CMake projects in NI):
   - Run `ctest -N --test-dir build` → parse test names into `discovered_tests`.
4. **Change-scoped match:** from diff / SPEC / `add_test` / binary names, compute `matched_change_tests`.
5. **Meaningful pass (CMake + dirty build files):** require matched tests executed **or** explicit `VerifyState` that change-tests absent → **fail closed in NI**.
6. Extend `VerificationRecord` with:
   - `reconfigured: bool`
   - `discovered_tests: list[str]`
   - `matched_change_tests: list[str]`
   - `cmake_reconfigure_command: Optional[str]`

#### Acceptance tests (`tests/basic/test_z_cmake_verify.py`)

- Temp tree: stale `build/` listing only `old_tests`; edited includes `CMakeLists.txt` adding `new_tests`; mock runners assert reconfigure invoked before ctest.
- Without reconfigure (`Z_CMAKE_RECONFIGURE=0`) document escape; with default on, refuse suite-only green when matched name missing from `ctest -N`.

#### Flags

| Env | Default | Meaning |
|-----|---------|---------|
| `Z_CMAKE_RECONFIGURE` | `1` | Reconfigure when build files edited |
| `Z_CMAKE_REQUIRE_MATCHED` | `1` in NI | Fail if change tests not in `ctest -N` |

---

## F3 — Sanitizer `tool_missing` is soft only

### Observed product failure

Tree correctly labels sanitizer not run / tool missing; unit tests pass; work still advances / looks successful in automation.

### Repo evidence

| Location | Behavior today |
|----------|----------------|
| `DynamicComparison.blocks_commit` | Only `no_improvement` / `regression`. |
| `DynamicComparison.soft_block` | Includes **`tool_missing`**, `reduced`, `after_only`. |
| `gate.py` ~646–669 | Hard list from `blocks_commit` only; soft → Medium honesty nodes via `_MEDIUM_GATE_TYPES` (`MEMORY_SAFETY`, `LEAK_ANALYSIS`, `DYNAMIC_ANALYSIS`, …). |
| Medium path | Needs ack interactively; under yes-always + `explicit_yes_required` → **silent `n`** (see F4) — or force paths that don’t elevate `tool_missing`. |

### Feature to implement: `sanitizer-teeth`

**Touch:** `dynamic_analysis.py`, `gate.py`, optional `recipe_runner.py`.

#### Behavior

1. **Policy** `Z_SANITIZER_POLICY=soft|hard`:
   - Default **`hard`** when `io.yes is True` or NI one-shot; **`soft`** interactive otherwise.
   - Hard: `tool_missing` (and required-but-not-run) → treat as **`blocks_commit`** / High / NI nonzero.
2. **Recipe prefer-execute:** if README / plan / SPEC / chat contains concrete  
   `cmake … -D*SAN=ON` / `ctest --test-dir build-asan` lines, parse and attempt run (budgeted) before leaving prose-only.
3. If tool truly unavailable under hard: list node ids + attempted commands; unit green must **not** substitute for sanitizer success.
4. Soft mode preserves today’s Medium honesty for interactive humans.

#### Acceptance tests (`tests/basic/test_z_sanitizer_policy.py`)

- Comparison with `outcome=tool_missing` + hard policy → `blocks_commit` True / gate allow_commit False.
- Soft policy → soft_block True, blocks_commit False.
- Recipe parser extracts a known cmake/ctest line from a fixture README snippet.

#### Flags

| Env | Default | Meaning |
|-----|---------|---------|
| `Z_SANITIZER_POLICY` | `hard` if yes-always/NI else `soft` | Teeth for tool_missing |
| `Z_SANITIZER_RECIPES` | `1` | Prefer execute discovered recipes |

---

## F4 — Gate block + `--yes-always` with no discoverable next step

### Observed product failure

Escalation “OVERRIDE…,” then “Commit blocked…,” session ends; dirty tree; escapes not printed.

### Repo evidence

| Location | Behavior today |
|----------|----------------|
| `io.confirm_ask` ~891–892 | **`if self.yes is True: res = "n" if explicit_yes_required else "y"`** — by design, force prompts cannot be auto-yes’d. |
| `gate.py` ~1307–1351 | High block → `confirm_ask(..., explicit_yes_required=True, default="n")` → under yes-always returns False → `allow_commit=False`. |
| `gate.py` ~72–74 | `Z_FORCE_COMMIT` / `--force-commit` **do** bypass when set **before** the prompt — but block path does not teach the user. |
| `base_coder` ~3117–3125 | `Commit blocked by Z verification gate. {detail}` — mentions `--force-commit` in generic detail sometimes; **does not** print `Z_FORCE_COMMIT`, `Z_SKIP_VERIFY_GATE`, dirty-tree status, or `Z_NI_GATE`. |
| EOF path in `confirm_ask` | Loud error when stdin EOF **and** `yes` is not True — yes-always never reaches it. |

### Feature to implement: `gate-ni-ux`

**Touch:** `gate.py` (shared block formatter), `base_coder.py` block path, brief comment/doc in `io.py`.

#### Behavior

1. **Always** on `allow_commit=False` print a fixed template:

   ```text
   Commit blocked by Z verification gate.
   Reason: …
   Working tree: DIRTY (N files). Commit did NOT happen.
   Non-interactive options:
     • Fix issues and re-run
     • Z_FORCE_COMMIT=1  — log override and commit (High still logged)
     • Z_SKIP_VERIFY_GATE=1 — disable gate (escape hatch)
     • Z_NI_GATE=block|force|reflect  — policy (default block)
   ```

2. When `yes is True` and override would be needed: **print the template first**, then apply `Z_NI_GATE`:
   - `block` (default): deny commit, ensure NI exit ≠ 0
   - `force`: same as `Z_FORCE_COMMIT=1` (logged)
   - `reflect`: set reflect_message summarizing blockers (no silent success)
3. Never rely on silent `n` alone as the user-visible outcome.

#### Acceptance tests (`tests/basic/test_z_gate_ni_ux.py`)

- Format helper always contains `Z_FORCE_COMMIT` and `Z_SKIP_VERIFY_GATE`.
- Simulated yes-always High block → message emitted; allow_commit False unless `Z_NI_GATE=force` / `Z_FORCE_COMMIT=1`.

#### Flags

| Env | Default | Meaning |
|-----|---------|---------|
| `Z_NI_GATE` | `block` | `block` / `force` / `reflect` |
| `Z_FORCE_COMMIT` | off | Existing; must be **printed** on block |
| `Z_SKIP_VERIFY_GATE` | off | Existing; must be **printed** on block |

---

## F5 — Skill miss / near-dup capture + Chroma telemetry spam

### Observed product failures

1. Many related skills on disk; task logs capability-gap, never “Applying skill(s)…,” then captures a near-identical bug_pattern.
2. Every session: `Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given`.

### Repo evidence

| Location | Behavior today |
|----------|----------------|
| `skills/session.retrieve_skill_candidates` | Chroma `max_distance=0.55`; on empty → `match_skills` keyword fallback. |
| `skills/index.relevance_score` | Token overlap on title/desc/tags/triggers — **does not** score `root_cause_category` / `fix_technique` / `symptom_description`; **no** stem folding (`lru`↔`lfu`↔`cache`↔`evict`). |
| Chroma hit + router skip | Can look like “no skill” in UI if verbose skip reasons not always shown. |
| `save_skill_from_task` | Always `_persist_skill` new skill; **no** near-dup merge/update. |
| Bug-pattern + yes-always | Auto-capture **allowed** (by design) — amplifies dup growth when retrieve missed. |
| `skills/vector.py` `_ensure` | `chromadb.PersistentClient(...)` with **no** `ANONYMIZED_TELEMETRY=False` / Settings kill switch. |
| `aider/analytics.py` | Unrelated; already guarded — spam is **Chroma product telemetry**, not Z analytics. |

### Feature to implement: `skill-retrieve` + `chroma-telemetry`

#### A. Lexical / category fallback (`skills/near_dup.py` + session/index)

1. When Chroma returns empty **or** top distance > threshold, run **fallback matcher**:
   - Fields: title, symptom, `root_cause_category`, `fix_technique`, tags
   - Stem / synonym folding map for cache eviction family (extensible)
   - Jaccard / token overlap threshold (configurable)
2. **Always log retrieve attempts** in verbose or NI: top-k distances + skip reasons (so silence ≠ “no index”).
3. **Before capture:** if similarity ≥ threshold to existing skill → **update** (append evidence, bump metadata) instead of new id; print `Updated existing skill: …`.
4. Optional later: `z skill dedupe --apply`.

#### B. Chroma telemetry silence (`skills/vector.py`, early env in `main`/`cli`)

1. Before `PersistentClient`:  
   `os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")`  
   plus any Chroma Settings `anonymized_telemetry=False` if API available.
2. Do not print Chroma telemetry TypeErrors to the coding session unless `Z_VERBOSE=1`.

#### Acceptance tests

- `test_z_skill_near_dup.py`: synthetic index with LRU eviction title; LFU SPEC retrieves via fallback; capture with near-dup title does not increase count.
- `test_z_chroma_telemetry.py`: init vector index with stub/env; assert telemetry env set; no TypeError leaked to captured stderr under default verbosity.

#### Flags

| Env | Default | Meaning |
|-----|---------|---------|
| `Z_SKILL_NEAR_DUP` | `1` | Merge near-dup captures |
| `Z_SKILL_LEXICAL_FALLBACK` | `1` | Category/stem matcher after Chroma miss |

---

## Cross-cutting design principles (implementation checklist)

1. **Non-interactive = contract** — exit code + one-line outcome reflect reality.
2. **Honesty with teeth** — labeled risks that matter in NI must block or nonzero-exit.
3. **Verify the change** — stale configure is not verification.
4. **Retrieve before invent** — update near-dups; don’t mint clones.
5. **Fail closed loudly** — every block prints exact overrides + dirty-tree state.
6. **Escape hatches remain** — never remove `Z_SKIP_VERIFY_GATE` / `Z_FORCE_COMMIT`; make them discoverable.
7. **No second coding agent** — keep thin control plane; these are orchestration contracts around the existing coder.

---

## Suggested implementation order (mergeable slices)

| # | Slice | Risk | Why first/later |
|---|-------|------|-----------------|
| 1 | **chroma-telemetry** | Tiny | Confidence win; stops session spam |
| 2 | **gate-ni-ux** | Low | Messaging + `Z_NI_GATE`; unblocks automation understanding |
| 3 | **ni-contract** | Medium | Exit codes + auto-seed; core false-completion claim |
| 4 | **verify-cmake** | Medium | Stale build honesty |
| 5 | **sanitizer-teeth** | Medium | Policy + optional recipes |
| 6 | **skill-retrieve** | Medium | Lexical fallback + near-dup merge |

Each slice: code + unit tests + short note in `coding-quality.md` or this doc’s “Implemented” section + env table row.

---

## Test matrix (mandatory before claiming done)

| Suite | Covers |
|-------|--------|
| `test_z_ni_contract.py` | exit codes, empty-edit failure, auto-seed reflect |
| `test_z_cmake_verify.py` | reconfigure on CMakeLists edit; refuse stale suite-only green |
| `test_z_sanitizer_policy.py` | tool_missing hard vs soft |
| `test_z_gate_ni_ux.py` | block message contains escapes; yes-always path prints hints |
| `test_z_skill_near_dup.py` | lexical retrieve + capture merge |
| `test_z_chroma_telemetry.py` | telemetry env / no TypeError spam on init |
| Existing uncertainty + coding-quality tests | no regressions |

---

## Non-goals

- Replacing SEARCH/REPLACE with a full native tool runtime.
- Auto-force-committing High nodes under yes-always by default.
- Guaranteeing sanitizer toolchains on every machine (hard-fail + message is enough).
- Rewriting the uncertainty tree or coding-quality stack.

---

## Success metric

> A tool whose pitch is “prevents false completion” must not exit 0 after doing nothing, must not call the wrong suite “verified,” must not soft-pedal missing sanitizers in automation, must reuse skills instead of cloning them, and must tell automation how to unblock a gate.

Until the slices above land in Python with tests, that claim is **aspirational**. This deep dive is the implementation checklist.

---

## Appendix — quick “is it implemented?” grep

```bash
rg -n 'Z_NI_REQUIRE_EDITS|Z_NI_GATE|Z_SANITIZER_POLICY|Z_CMAKE_RECONFIGURE|Z_SKILL_NEAR_DUP|ANONYMIZED_TELEMETRY|ni_contract|cmake_verify' aider/
# Expect: no hits in .py until slices merge
```

## Appendix — key line anchors (planning-time)

| Fault | Anchor |
|-------|--------|
| F1 exit 0 | `aider/main.py` `--message` / `--message-file` bare `return` |
| F1 plan auto | `base_coder._maybe_require_implementation_plan` `io.yes is True` → approve |
| F2 ctest | `verify.detect_test_command` → `ctest --test-dir build` |
| F3 soft | `DynamicComparison.blocks_commit` / `soft_block` |
| F4 silent n | `io.confirm_ask` `yes is True` + `explicit_yes_required` → `"n"` |
| F4 block msg | `base_coder` `Commit blocked by Z verification gate.` |
| F5 retrieve | `retrieve_skill_candidates` + `relevance_score` fields |
| F5 capture | `save_skill_from_task` always new persist |
| F5 telemetry | `SkillVectorIndex._ensure` PersistentClient without telemetry kill |
