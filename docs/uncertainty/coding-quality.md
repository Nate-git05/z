# Coding quality (tranche 1)

Z keeps its differentiators (skills, uncertainty, verify gate) but stops them
from crowding the **coding** turn. Patterns inspired by OpenCode / Claude Code
process design — not a clone of their runtimes.

| Mechanism | Default | Escape hatch |
|-----------|---------|--------------|
| Compact skill directives | on | `Z_SKILL_INJECT_FULL=1` |
| Shell/tool output budget | on (2000 lines / 50 KiB) | `Z_TOOL_OUTPUT_BUDGET=0` |
| Strict chat-file edits | on | `Z_STRICT_CHAT_EDITS=0` |
| Coding-quality reminder | implement modes | (tied to edit-capable modes) |

## What you should see

- Skill inject: short **directives** + path to full playbook on disk, not a wall of markdown.
- Fat `pytest` / build logs: preview in chat, full text under `$Z_HOME/tool-output/`.
- Edits to files not in chat: **blocked** (even with `--yes-always`); `/add` first.

## Related

- Plan: [coding-quality-tranche1-plan.md](./coding-quality-tranche1-plan.md)
- Skills: [../skills/README.md](../skills/README.md)
- Uncertainty: [README.md](./README.md)
