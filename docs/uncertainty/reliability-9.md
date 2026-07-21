# Reliability 9/10 — closing Codex evaluation gaps

**North-star metric: false completion rate.**

A 9/10 agent may stop and say it cannot verify something. It must almost never
say a feature is ready when its central journey is broken.

This document maps the Codex evaluation categories to concrete subsystems in
`aider/z/uncertainty/` and the priority order for getting each category to ≥9/10.

---

## Codex scores → target

| Category | Was | Target | Primary lever |
|----------|-----|--------|---------------|
| Skill selection | 8 | 9 | Capability plan above named skills |
| Code structure | 5 | 9 | Architecture checkpoint before files |
| Unit-level logic | 7 | 9 | Exact assertions + transition tables (ongoing) |
| User experience | 4 | 9 | Visible-state modeling + viewport checks (ongoing) |
| End-to-end correctness | 2 | 9 | Critical journey plan + typed evidence |
| Verification discipline | 3 | 9 | Verification integrity enforcement |
| Uncertainty safety | 7 | 9 | Integrity conflict-of-interest rule |
| Uncertainty accuracy | 3 | 9 | Evidence-typed nodes + failure layers |
| Unsupervised reliability | 3.5 | 9 | Completion gate (partial vs complete) |

---

## What shipped in this slice (P0)

### 1. Verification integrity (`integrity.py`)

Protected surfaces: `package.json` scripts, lockfiles, `tsconfig` strictness,
eslint/pytest/CI configs, git hooks.

After a failed check, diffs that:

- replace typecheck/test with `exit 0` / `echo ok`
- remove scripts
- disable `strict`
- add broad `@ts-ignore` / `eslint-disable`
- skip tests / weaken assertions
- set CI `continue-on-error`

…are **blocked** and raise `Verification Integrity` (High).

Invariant: *A failing verification command may be repaired, but its strength
may not be reduced without explicit human approval.*

### 2. Failure classification (`failure_classify.py`)

`tsc: command not found` → `command_not_found` (environment), **not** a
TypeScript error. Reflect prompts backtrack to install/deps, then re-run the
**original** check unchanged.

Layers: command_not_found, dependency_install, permission, network, type_error,
assertion, build_framework, timeout, flaky, unknown.

### 3. Capability plan (`capabilities.py`)

```
required = infer_capabilities(requirements)
available = skills + tools + native_abilities
gaps = required - available
```

Named skills are only one source. No skill ⇒ compensate with an explicit
workflow (e.g. multi-session browser), never “skip specialized verification.”

Injected at skill-router checkpoints even when retrieval returns nothing.

### 4. Architecture checkpoint (`architecture.py`)

Pre-coding checklist: shared state, runtime model, trust boundaries,
concurrency, persistence, contracts, failure recovery. Critical unknowns become
blocking assumptions. Recommends UI → typed client → auth route → service →
repository boundaries.

### 5. Critical user journeys (`journeys.py`)

Acceptance scenarios derived **before** implementation. Evidence is typed:

- multiplayer → `multi_session_e2e`
- auth → `browser_e2e`
- API → `integration_test`

A unit test of `respondChallenge()` **cannot** resolve a multi-session node.

### 6. Completion gate (`completion.py`)

Declares COMPLETE only when critical items pass. Otherwise:

> PARTIAL COMPLETION — The implementation and lower-level checks may be
> complete, but the central journey remains unverified.

Commit may still proceed for journey-only gaps (so work is not stranded), but
`claimed_complete=False`.

---

## Wiring

| Hook | Behavior |
|------|----------|
| `plan.triage_for_planning` | Also fires on architecture / CUJ signals |
| `draft_plan_from_request` | Fills capability + architecture + journeys + UX + transitions + multi-session |
| `base_coder._maybe_pull_skills` | Always builds capability plan |
| `gate.prepare_commit` | Integrity + artifacts + weak assertions + backtrack + clean-room + multi-session + completion |
| Reflect messages | Include failure layer + causal backtrack target |

---

## What shipped in P1 (this follow-up)

### 7. Evidence ledger (`evidence.py`)

Every check stores command, cwd, exit code, output excerpt, tree hash, env
assumptions, timestamp. Edits after a pass mark evidence **stale**.

### 8. Clean-room verification (`cleanroom.py`)

Discovers (and optionally runs with `Z_RUN_CLEANROOM=1`):

wipe → install from lockfile → typecheck → lint → unit → integration → build →
start → HTTP smoke.

### 9. Causal backtracking (`backtrack.py`)

Assumption chain: env → deps → types → behavior → build → journey.
Failure walks to the **earliest** unsupported parent. Proposed repairs that
touch the detector set `weaken_blocked`. `reopen_on_contradiction` clears
resolved nodes when new evidence conflicts.

### 10. UX visible-state model (`ux_states.py`)

Multiplayer/generic web state machines with per-state prompts (sees / can do /
loading / disabled / slow network / leave / timeout) plus viewport/a11y/
overflow checklist.

### 11. Exact assertions + transition tables (`assertions.py`)

Flags `a\|\|b` / `toBeTruthy` / `@ts-ignore` in tests. Infers challenge/match
transition tables and generates exact `toEqual` stubs.

### 12. Multi-session browser hooks (`browser_sessions.py`)

Plans two independent contexts. If tools unavailable or
`Z_MULTI_SESSION_E2E_CMD` unset → honest PARTIAL (never claim journey works).
When set, runs the project E2E and binds `multi_session_e2e` evidence.

### 13. Artifact hygiene (`artifacts.py`)

Blocks `.z/`, aider history, caches, scratch dumps from product commits.

### 14. Benchmark catalog (`benchmark.py` + `tests/reliability_benchmark/`)

12-task taxonomy with `score_task` / `false_completion_rate` aggregation —
not a single RPS tune.

---

## Remaining for production hardening (P2)

1. Drive clean-room by default on deploy-tagged tasks (not only `Z_RUN_CLEANROOM`).
2. Real Playwright multi-context runner when the project has no E2E script.
3. Auto-fill UX state answers from DOM inspection.
4. Property-based tests for compact rule systems.
5. Live calibration of detector noise (TP/FP/reopen rates) into thresholds.
6. Expand benchmark from stubs to full interactive agent evals.

---

## Evaluation principle

Do **not** tune Z to pass one multiplayer example. Measure across a benchmark:

- Functional success rate
- **False-completion rate** (primary)
- Verification-weakening rate
- Correct evidence-type resolution rate
- Reopened-node rate after contradictions
- Rate of correctly asking for human help

Env knobs:

| Env | Effect |
|-----|--------|
| `Z_RUN_CLEANROOM=1` | Execute clean-room install/build/smoke at commit gate |
| `Z_SKIP_CLEANROOM=1` | Skip clean-room execution |
| `Z_BROWSER_TOOL` | Force browser tool name, or `none` |
| `Z_MULTI_SESSION_E2E_CMD` | Project command that drives two-context E2E |
