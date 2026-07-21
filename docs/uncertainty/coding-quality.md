# Coding quality (tranche 1–4)

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

## Tranche 3

| Mechanism | Default | Escape hatch |
|-----------|---------|--------------|
| Strict SEARCH (no cross-file fallback) | on | `Z_STRICT_SEARCH=0` |
| AGENTS.md house instructions | on | `Z_HOUSE_INSTRUCTIONS=0` |
| Live P2 adapter skeleton | opt-in | `Z_P2_LIVE=1` + `--adapter live` |

### Strict SEARCH

SEARCH/REPLACE applies only to the named file. The legacy “try every other file
in chat” fallback is off by default.

### House instructions

Project (and ancestor) `AGENTS.md` plus optional `$Z_HOME/AGENTS.md` inject once
per session as a compact coding-context block.

### Live P2 adapter

```bash
Z_P2_LIVE=1 Z_P2_LIVE_HOOK=/path/to/hook.py \
  python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average
```

Without `Z_P2_LIVE=1`, selecting `--adapter live` returns a timed-out stub so CI
never spends tokens accidentally. Default remains the scripted adapter.

## Tranche 4

| Mechanism | Default | Escape hatch |
|-----------|---------|--------------|
| Compact plan-for-context inject | on | `Z_PLAN_CONTEXT_FULL=1` or `Z_CONTROL_PLANE_COMPACT=0` |
| Compact capability directive + dedupe | on | `Z_CONTROL_PLANE_COMPACT=0` |
| Tighter `/plan-exit` budget | 2500 chars | `Z_PLAN_EXIT_CHARS`, or compact off |

Human scrollback (`format_plan_for_user`) and `engine.ctx.plan` stay complete —
detectors still see the full artifact. Only `cur_messages` injects get thinner.

## Related

- Plans: [coding-quality-tranche1-plan.md](./coding-quality-tranche1-plan.md),
  [coding-quality-tranche2-plan.md](./coding-quality-tranche2-plan.md),
  [coding-quality-tranche3-plan.md](./coding-quality-tranche3-plan.md),
  [coding-quality-tranche4-plan.md](./coding-quality-tranche4-plan.md)
- Skills: [../skills/README.md](../skills/README.md)
- Uncertainty: [README.md](./README.md)
