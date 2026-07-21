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
| Explore pass (thin chat) | on (deep scout default) | `Z_EXPLORE_PASS=0`; `Z_EXPLORE_DEPTH=thin` |
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
bounded **explore scout** (rg/path heuristics + signature peeks + related-test
hints) — not a second agent. Set `Z_EXPLORE_DEPTH=thin` for the path-only list.

### Done soft-stop

If the model claims “done/fixed/ready” while High uncertainty nodes are open,
verify failed, or completion is PARTIAL, Z reflects instead of accepting the claim.

## Tranche 3

| Mechanism | Default | Escape hatch |
|-----------|---------|--------------|
| Strict SEARCH (no cross-file fallback) | on | `Z_STRICT_SEARCH=0` |
| AGENTS.md house instructions | on | `Z_HOUSE_INSTRUCTIONS=0` |
| Live P2 adapter | opt-in (`z` / `hook` / `replay`) | `Z_P2_LIVE=1` + `--adapter live` |

### Strict SEARCH

SEARCH/REPLACE applies only to the named file. The legacy “try every other file
in chat” fallback is off by default.

### House instructions

Project (and ancestor) `AGENTS.md` plus optional `$Z_HOME/AGENTS.md` inject once
per session as a compact coding-context block.

### Live P2 adapter

```bash
# Builtin Z coder (API keys required)
Z_P2_LIVE=1 Z_P2_LIVE_MODEL=gpt-4o-mini \
  python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average --no-baseline

# Example hook
Z_P2_LIVE=1 Z_P2_LIVE_HOOK=scripts/p2_live_hook_example.py \
  python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average --no-baseline

# Offline replay (no LLM)
Z_P2_LIVE=1 Z_P2_LIVE_BACKEND=replay \
  python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average --no-baseline
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

## Plan interview + tools + thin loop

| Mechanism | Default | Escape hatch |
|-----------|---------|--------------|
| Plan interview (clarify → draft → approve) | on | `Z_PLAN_INTERVIEW=0` |
| Tool-output budget beyond shell (`/run`, `/web`, MCP helper) | on | `Z_TOOL_OUTPUT_BUDGET=0` |
| Thin read-only tool-loop (` ```z-tool` ) | on | `Z_TOOL_LOOP=0` |

### Plan interview

```text
/plan fix the average off-by-one without touching auth
# … clarify questions …
# … user answers …
/plan-draft          # optional explicit advance
# … write plan under $Z_HOME/plans/ …
/plan-status
/plan-exit           # or /plan-approve
```

### Thin tool-loop

Model may request bounded read-only tools mid-turn:

````text
```z-tool
read calcpkg/ops.py
grep average --glob '*.py'
```
````

Z runs up to `Z_TOOL_LOOP_MAX` tools, budgets output, reflects, and defers
SEARCH/REPLACE apply for that round. Not a second agent.

## Related

- Plans: [coding-quality-tranche1-plan.md](./coding-quality-tranche1-plan.md),
  [coding-quality-tranche2-plan.md](./coding-quality-tranche2-plan.md),
  [coding-quality-tranche3-plan.md](./coding-quality-tranche3-plan.md),
  [coding-quality-tranche4-plan.md](./coding-quality-tranche4-plan.md),
  [coding-quality-explore-deep-plan.md](./coding-quality-explore-deep-plan.md),
  [coding-quality-plan-tools-loop.md](./coding-quality-plan-tools-loop.md)
- Skills: [../skills/README.md](../skills/README.md)
- Uncertainty: [README.md](./README.md)

