# Plan interview + tool-output + thin tool-loop

Stacked on deeper-explore. Still one coder + thin control plane.

## 1. Plan interview workflow

`/plan` becomes clarify → draft → approve, not only a permission mode.

| Stage | Behavior |
|-------|----------|
| `clarify` | Ask 1–3 clarifying questions; no plan file required yet |
| `draft` | Write/update plan artifact under `$Z_HOME/plans/` |
| `ready` | Plan exists; `/plan-exit` (or `/plan-approve`) loads binding context → implement |

Commands: `/plan`, `/plan-draft`, `/plan-approve` (alias of exit when ready), `/plan-exit`, `/plan-status`.
Escape: `Z_PLAN_INTERVIEW=0` restores sticky permission-only mode.

## 2. Tool-output beyond shell

Route **every** large dump destined for `cur_messages` through `budget_tool_output`:
- `/run` / `!` command output
- `/web` scrape content
- MCP / generic tool results via `inject_tool_result()`

Same env knobs as shell (`Z_TOOL_OUTPUT_BUDGET`, lines/bytes).

## 3. Thin native tool-loop (read-only)

Bounded in-turn read tools before SEARCH/REPLACE — **not** a peer agent rewrite.

Model may emit:

````
```z-tool
read path/to/file.py
grep average --glob '*.py'
glob **/ops.py
```
````

Coder runs up to `Z_TOOL_LOOP_MAX` (default 3) read-only tools, budgets output, reflects
once with results, and defers edit apply that round. Mutating tools rejected.

Escape: `Z_TOOL_LOOP=0`.

## Non-goals

Full OpenCode tool runtime, explore subagent process, auto-approving MCP writes.
