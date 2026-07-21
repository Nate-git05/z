# Coding quality (tranche 1 + 2)

Z keeps its differentiators (skills, uncertainty, verify gate) but stops them
from crowding the **coding** turn. Patterns inspired by OpenCode / Claude Code
process design — not a clone of their runtimes.

## Tranche 1

| Mechanism | Default | Escape hatch |
|-----------|---------|--------------|
| Compact skill directives | on | `Z_SKILL_INJECT_FULL=1` |
| Shell/tool output budget | on (2000 lines / 50 KiB) | `Z_TOOL_OUTPUT_BUDGET=0` |
| Strict chat-file edits | on | `Z_STRICT_CHAT_EDITS=0` |
| Coding-quality reminder | implement modes | (tied to edit-capable modes) |

## Tranche 2

| Mechanism | Default | Escape hatch |
|-----------|---------|--------------|
| `/plan` permission mode | on | `Z_PLAN_MODE=0` |
| Explore pass (thin chat) | on | `Z_EXPLORE_PASS=0` |
| Done soft-stop | on | `Z_DONE_SOFT_STOP=0` |

### Plan mode

```text
/plan fix the average off-by-one without touching auth
# … Z explores read-only, writes a plan under $Z_HOME/plans/ …
/plan-exit
# … loads plan as binding context, switches to implement …
```

Product SEARCH/REPLACE is blocked while `TaskMode.PLAN` is active.

### Explore pass

When fewer than 3 files are in chat, IMPLEMENT/PLAN/INVESTIGATE turns get a
compact candidate-file list (rg/path heuristics) — not a second agent.

### Done soft-stop

If the model claims “done/fixed/ready” while High uncertainty nodes are open,
verify failed, or completion is PARTIAL, Z reflects instead of accepting the claim.

## Related

- Plans: [coding-quality-tranche1-plan.md](./coding-quality-tranche1-plan.md),
  [coding-quality-tranche2-plan.md](./coding-quality-tranche2-plan.md)
- Skills: [../skills/README.md](../skills/README.md)
- Uncertainty: [README.md](./README.md)
