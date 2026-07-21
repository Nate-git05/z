# P1 — Precise evidence and node lifecycles

Depends on [P0 control flow](./p0-control-flow.md).

| Item | Module(s) | What changed |
|------|-----------|--------------|
| P1.1 TaskClause | `clause.py`, `intent.py`, `checklist.py`, `plan.py` | Typed clauses; checklist only from requested_action / acceptance_criterion; constraints block plan steps; process rules checked via session evidence |
| P1.2 Resolution contracts | `resolution.py`, `store.py` | Every node gets a contract; temporary blockers don't merge across sessions; shell-approval auto-resolves; blocker explanation surface |
| P1.3 Exception classes | `aider/z/errors.py`, planning gate | Optional / Recoverable / IntegrityGate; unclassified → fail closed; planning no longer silently continues |

Tests: `tests/basic/test_z_p1_evidence_lifecycle.py`
