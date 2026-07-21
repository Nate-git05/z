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
| `draft_plan_from_request` | Fills capability + architecture + journeys |
| `base_coder._maybe_pull_skills` | Always builds capability plan |
| `gate.prepare_commit` | Integrity scan + completion evaluation |
| Reflect messages | Include failure layer + backtrack target |

---

## Next slices (P1 / P2)

Ordered for fastest false-completion reduction:

1. **Clean-room completion** — remove node_modules/dist → install from lockfile →
   typecheck → lint → unit → integration → build → start → smoke; store evidence
   records (command, cwd, exit, tree hash, staleness).
2. **Causal backtracking store** — parent/child uncertainty nodes; reopen earliest
   contradicted assumption; invalidate dependent evidence.
3. **UX visible-state model** — per-user state machine prompts + viewport /
   a11y checklist for UI tasks.
4. **Exact unit assertion detector** — flag `toBeTruthy` / `a \|\| b` alternatives
   on new tests; prefer transition-table generation for state machines.
5. **Multi-session browser automation** — separate contexts for collaborative
   features when tools exist; otherwise honest partial completion.
6. **Artifact hygiene** — block agent histories/caches from product commits.
7. **Benchmark suite** — new apps, feature adds, concurrency, migrations, auth,
   misleading bugs, dep failures, wrong-tests-vs-wrong-code, multi-user,
   process-instruction tasks, stop-and-ask tasks.

---

## Evaluation principle

Do **not** tune Z to pass one multiplayer example. Measure across a benchmark:

- Functional success rate
- **False-completion rate** (primary)
- Verification-weakening rate
- Correct evidence-type resolution rate
- Reopened-node rate after contradictions
- Rate of correctly asking for human help
