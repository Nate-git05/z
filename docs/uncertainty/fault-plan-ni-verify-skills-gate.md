# Spec: Non-interactive continuity, verify honesty, skill retrieval, gate UX

**Status:** planning doc; runtime slices shipped separately (see § Suggested PR
slice order — `skill-retrieve` lands with this companion implementation).  
**Triggered by:** Claude Code writeup of live faults on `miniregex` / `minilfu`  
**Thesis (confirmed in code):** The model can write correct C++; the **orchestration /
product layer** is where false-completion-shaped silence, wrong-suite “verify,”
soft-blocked sanitizers, skill non-retrieval, and undiscoverable gate exits live.

---

## Fault → root cause map (repo evidence)

### F1 — Non-interactive first run: plan then silence (exit 0, no edits)

**Observed:** `--yes-always --message-file SPEC.md` prints capability/architecture
plan, “Plan approved — proceeding with implementation,” then model prose
“Please add these files…,” process exits 0, zero commits.

**Code path:**

| Step | Location | Behavior |
|------|----------|----------|
| One-shot CLI | `aider/main.py` `--message-file` | `coder.run(with_message=…)` then **return** (exit 0) |
| High-stakes plan | `base_coder._maybe_require_implementation_plan` | Under `io.yes is True`, plan auto-approves without interactive confirm |
| Same turn | LLM reply | Often asks to `/add` files instead of emitting SEARCH/REPLACE |
| File mentions | `check_for_file_mentions` | Confirm “Add file?” — with yes-always **does** auto-add *if* basename matches repo files; new paths / vague mentions often produce **no** reflect |
| Empty chat + strict edits | `allowed_to_edit` + `Z_STRICT_CHAT_EDITS` | Existing files not in chat blocked; **new** files still creatable — but model often won’t create without `/add` dance |
| Success signal | `main.py` | Completing `--message-file` always returns success **even if `aider_edited_files` is empty** |

**Root causes (product, not model):**

1. **No “work done” exit contract** for non-interactive runs.
2. **Plan auto-approve + empty chat** leaves the model in “ask human to add files” mode on the first implement turn.
3. **One-shot** does not treat “please add files” / zero-edit replies as a recoverable reflect (or as a hard failure).

---

### F2 — Verify claims pass while testing the wrong suite / skipping sanitizers

**Observed:** After editing CMake to add `minilfu_tests`, log shows only
`miniregex_tests` under existing `build/`, “100% passed.” README sanitizer
commands appear only as markdown Z wrote, never as executed `ctest`. Uncertainty
tree correctly emits “sanitizer not run / tool_missing,” then work still advances.

**Code path:**

| Step | Location | Behavior |
|------|----------|----------|
| Default C/C++ test cmd | `verify.detect_test_command` | If `CMakeLists.txt` **and** `build/` exist → `ctest --test-dir build` **with no reconfigure** |
| CMakeLists edited | (missing) | **No** `cmake -S . -B build` / invalidate-stale-build step when `CMakeLists.txt` ∈ edited |
| Relevant tests | `find_relevant_tests` | Name/path heuristics; does not prove ctest discovered the **new** target |
| Sanitizer taxonomy | `dynamic_analysis.DynamicRiskComparison` | `tool_missing` → **`soft_block` only**; `blocks_commit` is only `no_improvement` / `regression` |
| Gate | `gate.prepare_commit` | Soft sanitizer nodes → Medium / honesty, **not** hard fail-closed; does not auto-run README/`-DMINILFU_*SAN` recipes Z just authored |

**Root causes:**

1. **Stale build directory treated as authoritative** after build-system edits.
2. **“Tests passed” ≠ “tests for this change discovered and executed.”**
3. **Honesty without teeth:** `tool_missing` is labeled correctly but does not block
   non-interactive completion or force a discovered sanitizer command.

---

### F3 — Skill library: no retrieve hit, then near-duplicate capture

**Observed:** Many LRU/cache eviction skills in `~/.z/skills`; LFU task logs
“Capability gaps… no skill ≠ skip verification,” never “Applying skill(s)…,”
then invents a near-identical bug-pattern title and saves another duplicate.

**Code path:**

| Step | Location | Behavior |
|------|----------|----------|
| Retrieve | `skills/session.retrieve_skill_candidates` | Chroma `max_distance=0.55` + keyword fallback; bug_pattern pool only when `task_is_bugfix_intent` |
| Route | `skills/router.route_skills` | Can skip on score / needs_review / wrong stack |
| Capture | skill suggest/capture under yes-always | Bug-pattern capture **allowed** under yes-always; playbooks skipped |
| Dedup | (weak / missing) | No title/symptom near-dup gate before write; library grows copies |

**Root causes:**

1. Embedding/distance + “LRU vs LFU” wording fails cross-generalization.
2. Capability-gap messaging fires when retrieve returns empty — correct, but
   no **fallback lexical/category match** on `root_cause_category` /
   `fix_technique`.
3. Capture path does not **merge/update** near-duplicates.

---

### F4 — Commit gate + `--yes-always`: blocks with no discoverable next step

**Observed:** Escalation panel “OVERRIDE: force commit… awaiting reply,” then
“Commit blocked…,” session stops; uncommitted good work. Escape hatches
(`Z_SKIP_VERIFY_GATE`, force commit) not printed.

**Code path:**

| Step | Location | Behavior |
|------|----------|----------|
| Force prompt | `gate.py` ~1317 | `confirm_ask(..., explicit_yes_required=True, default="n")` |
| `--yes-always` | `io.confirm_ask` | **`yes is True` + `explicit_yes_required` → answers `"n"`** (by design) |
| Block message | `base_coder` after `allow_commit=False` | Generic “Commit blocked…” — **no** env/flag hints |
| Non-interactive EOF | `io.confirm_ask` | Loud error for EOF; yes-always path never reaches that — silent **n** |

**Root causes:**

1. Fail-closed is correct for High; **messaging and non-interactive contract are wrong.**
2. Automation has no **documented, surfaced** resolution: leave dirty + nonzero
   exit, or print exact overrides, or `Z_NI_GATE=reflect|block|force` policy.

---

### F5 — Chroma telemetry spam every session

**Observed:** `Failed to send telemetry event ClientStartEvent: capture() takes 1
positional argument but 3 were given`.

**Code path:** Chroma’s `chromadb/telemetry/product/posthog.py` `capture(self, event)`
vs older PostHog-style 3-arg calls inside Chroma — **dependency mismatch**, not
Z’s `aider/analytics.py` (which already try/excepts). Fires whenever skills/
Chroma init.

**Root cause:** Don’t call Chroma with telemetry enabled; set
`ANONYMIZED_TELEMETRY=False` (or equivalent) at skill-vector init; optionally
pin/suppress Chroma product telemetry.

---

## Design principles for the fix set

1. **Non-interactive = contract, not hope.** Exit code and final summary must
   reflect edits / verify / gate outcome.
2. **Honesty with teeth.** If the tree says sanitizer missing and the task
   required it, NI mode must not look like success.
3. **Verify the change, not the leftover build.** Build-system file edits
   invalidate cached configure.
4. **Retrieve before invent.** Near-dup skills update in place; don’t mint clones.
5. **Fail closed loudly.** Every block prints the exact override and the path
   left on disk.

---

## Workstreams (implementation order)

### P0 — Non-interactive run contract (`ni-contract`)

**Goals**

- After `--message` / `--message-file` / scripted runs:
  - If **zero** product files edited → **exit ≠ 0** (unless task mode is
    ask/diagnose/review by classification).
  - Print a one-line **Run outcome:** `edited=N verify=… commit=…`.
- When model asks to add files / mentions paths and chat is empty:
  - **Auto-seed** from explore scout + SPEC path mentions + `allowed` globs
    (new helper), then **reflect once** to implement (count toward
    `max_reflections`, raise NI default reflections to ≥5).
- Under `--yes-always`, treat “Please add these files to the chat” prose as
  a **structured miss** → auto `/add` candidates or create-new-file path,
  not silent success.

**Acceptance**

- Fixture: empty CMake project + SPEC requiring new `src/foo.c`; NI run must
  create files or exit nonzero.
- Exit 0 only if `len(aider_edited_files) > 0` or classified non-edit mode
  completed with an artifact (diagnosis text / review findings).

**Files (expected):** `aider/main.py`, `aider/coders/base_coder.py`,
new `aider/z/ni_contract.py`, tests under `tests/basic/test_z_ni_contract.py`.

---

### P0 — Verify: stale CMake + change-scoped tests (`verify-cmake`)

**Goals**

- If edited set intersects build system files (`CMakeLists.txt`,
  `CMakeLists.txt.in`, `*.cmake`, maybe `Makefile` rules), **reconfigure**
  before `ctest`:
  - Prefer: `cmake -S <src> -B <build>` using existing build dir’s cache
    compiler flags when present; else create `build/`.
- After reconfigure, assert **new test names** appear in
  `ctest -N --test-dir build` output when SPEC/edits introduced them
  (heuristic: symbols / `add_test` / binary names from diff).
- Record in `VerificationRecord`: `reconfigured=bool`,
  `discovered_tests=[…]`, `matched_change_tests=[…]`.
- **Meaningful pass** requires either matched change tests executed, or
  explicit `VerifyState` that change-tests were absent (fail closed in NI).

**Acceptance**

- Repro: existing `build/` with only `miniregex_tests`; edit CMakeLists to add
  `minilfu_tests`; verify must reconfigure and run (or hard-fail if still
  undiscovered), never report green on old suite alone.

**Files:** `aider/z/uncertainty/verify.py`, new
`aider/z/uncertainty/cmake_verify.py`, gate wiring, tests.

---

### P0 — Sanitizer: soft → hard when required (`sanitizer-teeth`)

**Goals**

- Policy (env `Z_SANITIZER_POLICY=soft|hard`, default **`hard` in NI /
  `--yes-always`**, soft interactive unless SPEC/checklist requires):
  - `tool_missing` / required-but-not-run → **blocks_commit** in hard mode.
- When README / plan / SPEC contains concrete `cmake … -D*SAN=ON` /
  `ctest --test-dir build-asan` recipes, **prefer executing those** over
  writing them back as prose (parse + run with budgeted output).
- If tool truly unavailable: NI exit ≠ 0 with node ids listed; do not pretend
  unit tests substituted.

**Acceptance**

- Dynamic-risk C edit + hard policy + no sanitizer → commit blocked and NI
  nonzero; log shows attempted discovery commands.

**Files:** `dynamic_analysis.py` (`blocks_commit` / soft_block), `gate.py`,
optional `aider/z/uncertainty/recipe_runner.py`.

---

### P1 — Gate UX for automation (`gate-ni-ux`)

**Goals**

- On any commit block, always print:

  ```
  Commit blocked by Z verification gate.
  Reason: …
  Working tree: DIRTY (N files). Commit did NOT happen.
  Non-interactive options:
    • Fix issues and re-run
    • Z_FORCE_COMMIT=1  — log override and commit (High still logged)
    • Z_SKIP_VERIFY_GATE=1 — disable gate (escape hatch)
    • Z_NI_GATE=block|force|reflect  — policy (default block)
  ```

- `--yes-always` must **not** silently answer `n` to override without printing
  the above (today it answers `n` via `explicit_yes_required`).
- Optional: `Z_NI_GATE=force` for trusted CI after green verify+sanitizer.

**Acceptance**

- NI run that hits High gate: exit ≠ 0, message contains `Z_FORCE_COMMIT` and
  `Z_SKIP_VERIFY_GATE` substrings; tree remains dirty unless force set.

**Files:** `gate.py`, `io.py` (document yes-always + explicit_yes interaction),
`base_coder.py` block path.

---

### P1 — Skill retrieve + near-dup consolidation (`skill-retrieve`) — IMPLEMENTED

**Detail plan:** [skill-retrieve-impl-plan.md](./skill-retrieve-impl-plan.md)

**Shipped**

1. **Fallback matcher** (`aider/z/skills/near_dup.py`): stem-folded token
   overlap on title/symptom/`root_cause_category`/`fix_technique` when Chroma
   is empty or weak (`Z_SKILL_CHROMA_WEAK`).
2. **RetrieveTrace** logged under verbose, `--yes-always`, or
   `Z_SKILL_RETRIEVE_LOG=1` (plus skip reasons under NI).
3. **Capture merge:** near-dup bug_patterns update existing id
   (`Updated existing skill: …`) instead of minting a clone.
4. Optional CLI `z skill dedupe --apply` still deferred.

**Acceptance (tests)**

- `test_z_skill_near_dup.py`: LFU task retrieves LRU sibling via lexical;
  capture near-dup keeps skill count stable; `Z_SKILL_NEAR_DUP=0` creates new.

**Files:** `skills/near_dup.py`, `skills/session.py`, `skills/cli.py`,
`coders/base_coder.py`.

---

### P2 — Chroma telemetry silence (`chroma-telemetry`)

**Goals**

- At vector index init: `os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")`
  (and Chroma’s documented kill switches).
- Swallow/ignore product telemetry errors; never print to coding session
  unless `Z_VERBOSE=1`.

**Acceptance**

- Fresh session with skills/Chroma: no `ClientStartEvent` / `capture()` TypeError
  lines on stderr.

**Files:** `skills/vector.py`, maybe `main.py` early env.

---

## Non-goals

- Replacing SEARCH/REPLACE with a full native tool runtime.
- Auto-force-committing High nodes under yes-always by default.
- Guaranteeing sanitizer install on every machine (hard-fail + message is enough).

---

## Test plan (mandatory before merge)

| Suite | Covers |
|-------|--------|
| `test_z_ni_contract.py` | exit codes, empty-edit failure, auto-seed reflect |
| `test_z_cmake_verify.py` | reconfigure on CMakeLists edit; refuse stale suite-only green |
| `test_z_sanitizer_policy.py` | tool_missing hard vs soft |
| `test_z_gate_ni_ux.py` | block message contains escapes; yes-always → n still prints hints |
| `test_z_skill_near_dup.py` | lexical retrieve + capture merge |
| `test_z_chroma_telemetry.py` | no telemetry TypeError on init |
| Existing P0/P1/P2 + coding-quality | no regressions |

---

## Rollout / flags

| Env | Default | Meaning |
|-----|---------|---------|
| `Z_NI_REQUIRE_EDITS` | `1` | Nonzero exit if NI implement run edits nothing |
| `Z_NI_GATE` | `block` | `block` / `force` / `reflect` |
| `Z_SANITIZER_POLICY` | `hard` when yes-always else `soft` | Teeth for tool_missing |
| `Z_CMAKE_RECONFIGURE` | `1` | Reconfigure when build files edited |
| `Z_SKILL_NEAR_DUP` | `1` | Merge near-dup captures |
| `Z_SKILL_LEXICAL_FALLBACK` | `1` | Folded lexical matcher after miss/weak Chroma |
| `Z_SKILL_LEXICAL_THRESHOLD` | `0.28` | Min lexical score |
| `Z_SKILL_CHROMA_WEAK` | `0.45` | Score below → also run lexical |
| `Z_SKILL_RETRIEVE_LOG` | off | Force retrieve trace even if not verbose/NI |
| `Z_SKIP_VERIFY_GATE` | off | Existing escape (must be **printed** on block) |
| `Z_FORCE_COMMIT` | off | Existing escape (must be **printed** on block) |

---

## Suggested PR slice order

1. **chroma-telemetry** — tiny, confidence win  
2. **gate-ni-ux** — messaging only  
3. **ni-contract** — exit codes + auto-seed  
4. **verify-cmake** — stale build  
5. **sanitizer-teeth** — policy  
6. **skill-retrieve** — lexical fallback + near-dup  

Each slice independently mergeable with tests.

---

## Success metric (product claim)

> A tool whose pitch is “prevents false completion” must not exit 0 after
> doing nothing, must not call the wrong suite “verified,” must not soft-pedal
> missing sanitizers in automation, must reuse skills instead of cloning them,
> and must tell automation how to unblock a gate.

This spec is the checklist for that claim.
