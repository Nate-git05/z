# Coding quality tranche 1 — plan

**Goal:** Match OpenCode/Claude-Code *process* quality (clean coder context, budgeted
tool output, read-before-edit) without dropping Z differentiators (skills,
uncertainty, verify gate). Those stay a **control plane**; the coding turn stays thin.

**Non-goals this tranche:** native tool-loop rewrite, explore subagents, full plan-mode
permission matrix, copying third-party prompts.

---

## Current Z pain (from code)

| Path | What happens today | Why coding suffers |
|------|--------------------|--------------------|
| `format_skills_for_context` | Injects **full** skill markdown into `cur_messages` | Crowds the coder with playbooks |
| `handle_shell_commands` | Folds **entire** command output into chat | Context rot from tests/logs |
| `allowed_to_edit` | Confirm “edit file not in chat?” — **`--yes-always` auto-approves** | Invented patches on unread files |
| Uncertainty / plan | Binding plan + capability blocks in chat | Necessary, but skills make it worse |

OpenCode equivalents we port as *ideas*:
- Compact directives, not full skill dumps (`format_skills` → directive)
- `Truncate` tool output → disk + preview (`output_budget`)
- Edit requires prior read / chat membership (strict chat-file gate)

---

## Design

### 1. Compact skill / capability injection

- Default: inject **directive** per skill — title, kind, languages, capability,
  description, truncated body (budgeted), path to full skill on disk.
- Escape hatch: `Z_SKILL_INJECT_FULL=1` restores today’s full-body inject.
- Capability plan stays (already compact); no change to uncertainty tree storage.
- Optional short **coding-quality reminder** in `final_reminders` for implement modes only.

### 2. Shell / tool output budget

- New module `aider/z/output_budget.py`.
- Limits (OpenCode-aligned defaults): **2000 lines** / **50 KiB** preview.
- Oversize → write full text under `$Z_HOME/tool-output/`, return head+tail preview
  + absolute path for the model to reason about without eating context.
- Wire into `Coder.handle_shell_commands` before output is added to chat.
- Env overrides: `Z_TOOL_OUTPUT_MAX_LINES`, `Z_TOOL_OUTPUT_MAX_BYTES`,
  `Z_TOOL_OUTPUT_BUDGET=0` to disable.

### 3. Strict read-before-edit (chat-file gate)

- Default **on** (`Z_STRICT_CHAT_EDITS=1`): existing files may only be edited if
  already in `abs_fnames` (chat). No confirm dialog that `--yes-always` can bypass.
- New file creation still allowed (with existing confirm / fabrication checks).
- Disable with `Z_STRICT_CHAT_EDITS=0` for legacy confirm behavior.
- On block: tool error + reflect message telling the model/user to `/add` the file.

### 4. Keep Z different

- Skills still retrieve/route/inject — just thinner.
- Uncertainty engine, verify gate, plan confirm unchanged.
- Modes (ASK/INVESTIGATE/IMPLEMENT) still gate planning/edits.

---

## Files to touch

| File | Change |
|------|--------|
| `aider/z/coding_context.py` | **new** — compact skill format + coding reminder + env flags |
| `aider/z/output_budget.py` | **new** — persist + preview |
| `aider/z/skills/session.py` | use compact formatter by default |
| `aider/coders/base_coder.py` | budget shell output; strict `allowed_to_edit`; coding reminder |
| `tests/basic/test_z_coding_quality.py` | **new** unit coverage |
| `docs/uncertainty/coding-quality.md` | operator-facing notes |

## Acceptance

- Skill inject without `Z_SKILL_INJECT_FULL` is ≪ full content length for long skills.
- Shell output > limits produces a file under `tool-output/` and a short preview.
- Editing an on-disk file not in chat is blocked under default strict mode (even with yes=True).
- P0/P1/P2 suites still green.
