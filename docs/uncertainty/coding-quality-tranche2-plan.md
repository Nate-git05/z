# Coding quality tranche 2 — plan

Builds on tranche 1 (compact skills, output budget, strict chat edits).
Still keeps Z differentiators; still does **not** rewrite Z into OpenCode's tool runtime.

## Goals (priority order from roadmap)

1. **Plan as a permission mode** — `/plan` enters a mode where product edits are denied; only a plan artifact may be written. `/plan-exit` (or approve) returns to implement with the plan as binding context.
2. **Verify-before-done soft stop** — if the model claims done while High uncertainty nodes are open or the last verify failed, reflect instead of silently accepting “done.”
3. **Optional explore pass** — read-only keyword/path search that injects a compact findings block before coding when the chat is empty/thin.

Non-goals: live P2 LLM adapter (tranche 2.5+), explore *subagent* process, full OpenCode plan-file interview workflow.

---

## Design

### A. `TaskMode.PLAN`

| Policy | PLAN |
|--------|------|
| `allows_edits` | False for product files |
| `allows_plan_file_edits` | True (only under plan dir) |
| `allows_planning` | True (draft plan) |
| `allows_capability_inference` | False |
| `skills_read_only` | True |
| shell mutation | False |

Commands:
- `/plan [prompt]` — force PLAN mode for this turn (or sticky switch with no args)
- `/plan-exit` — clear PLAN sticky; if a drafted plan exists, inject compact binding context and switch to code mode

Plan artifact path: `$Z_HOME/plans/<session-or-stamp>.md` (and optionally `.z/plans/` in repo).

`allowed_to_edit` / apply path: when `task_mode is PLAN`, reject any edit whose path is not under the plan directory.

### B. Soft done-gate

`aider/z/uncertainty/done_gate.py`:
- `looks_like_done_claim(text) -> bool`
- `soft_stop_reason(store, *, last_verify_failed, plan_pending) -> str | None`
- Wired from coder after a turn that produced assistant text claiming completion (and/or after edits) when High open nodes or failed verify exist → set `reflected_message`.

Default on: `Z_DONE_SOFT_STOP=1`.

### C. Explore pass

`aider/z/explore.py`:
- Keyword extraction from user task (identifiers, path-ish tokens)
- Bounded `rg`/`Path` search under project root
- Returns compact markdown: candidate paths + 1-line hints
- Injected into `cur_messages` at turn start for IMPLEMENT/PLAN when `len(abs_fnames) < N` (default 2)
- Default on: `Z_EXPLORE_PASS=1`; disable with `0`

---

## Files

| File | Change |
|------|--------|
| `aider/z/task_mode.py` | Add `PLAN` |
| `aider/z/plan_mode.py` | **new** — plan dir helpers, edit allowlist |
| `aider/z/explore.py` | **new** — read-only explore |
| `aider/z/uncertainty/done_gate.py` | **new** — soft stop |
| `aider/commands.py` | `/plan`, `/plan-exit` |
| `aider/coders/base_coder.py` | wire explore, plan edit gate, done soft-stop |
| `tests/basic/test_z_coding_quality_t2.py` | coverage |
| `docs/uncertainty/coding-quality.md` | update |

## Acceptance

- `/plan` turn cannot apply product-file SEARCH/REPLACE; can write plan file.
- Claiming “done” with open High nodes triggers a reflect soft-stop.
- With empty chat + explore on, implement turn gets a compact findings block.
- Tranche 1 + P0/P1 suites still green.
