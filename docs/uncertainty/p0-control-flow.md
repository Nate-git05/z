# P0 — Agent control flow

Shipped fixes for the control-flow root causes described in the P0 spec:

| Item | Module(s) | What changed |
|------|-----------|--------------|
| P0.1 Task modes | `aider/z/task_mode.py`, `base_coder.run_one`, `/ask` `/context` | Mode gates planning / checklist / capability / edits |
| P0.2 Structured intent | `aider/z/uncertainty/intent.py`, `plan.py` | Planner reads `TaskIntent` only |
| P0.3 Capabilities | `aider/z/uncertainty/capabilities.py` | Infer from classified requirements + provenance |
| P0.4 Async sync | `sync_outbox.py`, `store.py`, `remote.py` | Local write + background sync; `(1,2)` timeouts |
| P0.5 Shell risk | `aider/z/shell_risk.py`, `handle_shell_commands` | Risk classes; read-only / declared auto-approve |
| P0.6 Transcripts | `tests/basic/test_z_p0_control_flow.py` | Orchestration harness + scenarios |

See also: [README](./README.md), [reliability-9](./reliability-9.md).
