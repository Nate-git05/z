# Coding quality tranche 4 — plan

Stacked on tranche 3. Still one coder + thin control plane — not a tool-loop rewrite.

## Goal

**Control-plane noise budget** — keep skills/uncertainty/verify, but stop dumping
full plans, capability essays, and plan-exit bodies into the *coding* turn.

Human scrollback and `engine.ctx.plan` stay complete (detectors use the object).
Only `cur_messages` injects get compacted.

## Goals

1. **Compact approved-plan inject** — `format_plan_for_context` becomes a short
   binding directive (approach/steps/invariants/established solutions + gap
   summaries). Full dump via `Z_PLAN_CONTEXT_FULL=1`.
2. **Compact capability inject + dedupe** — thinner capability block in skill
   pulls; skip re-inject when the gap set is unchanged.
3. **Tighter `/plan-exit` context** — lower default budget with head+tail truncate.

Non-goals: live P2 hook implementation, explore subagent, native tool-loop,
changing detector logic or plan *generation*.

---

## Design

### A. `aider/z/control_plane_budget.py`

| Flag | Default | Effect |
|------|---------|--------|
| `Z_CONTROL_PLANE_COMPACT` | on | Master switch for compact coding injects |
| `Z_PLAN_CONTEXT_FULL=1` | off | Restore legacy full `format_plan_for_context` |
| `Z_PLAN_EXIT_CHARS` | 2500 | Budget for `/plan-exit` inject |

### B. Compact plan-for-context

Keep title, approach, steps, out-of-scope (capped), contracts, invariants,
ambiguities, established solutions. Summarize architecture / journeys / UX /
transitions / multi-session as one-liners. Point to scrollback for the full
human-facing plan.

### C. Capability directive

List required IDs + gap compensation tips only (no long essays). Fingerprint
`required ∪ gaps`; skip duplicate inject on reflect checkpoints.

### D. Plan-exit

Truncate freeform plan artifact to budget with head+tail (same idea as tool
output budget).

---

## Acceptance

- Compact `format_plan_for_context` ≪ full length on a rich plan; still includes
  established-solution prefer strings used by tests.
- Capability re-pull with identical gaps does not append another block.
- `/plan-exit` inject respects the char budget.
- Prior coding-quality + P0/P1/P2 suites green.
