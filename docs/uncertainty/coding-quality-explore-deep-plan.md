# Coding quality — deeper explore scout

Stacked on the live-P2 / coding-quality chain. Still **one coder** — not an
explore subagent process.

## Goal

Upgrade the thin keyword/rg explore pass into a **bounded read-only scout**:
candidate files plus signature peeks and related-test hints, still compact
enough for `cur_messages`.

## Design

| Piece | Behavior |
|-------|----------|
| `Z_EXPLORE_PASS=0` | Disable entirely (unchanged) |
| `Z_EXPLORE_DEPTH=thin` | Legacy candidate-path list only |
| `Z_EXPLORE_DEPTH=deep` | **Default** — candidates + signature peeks + related paths |
| `Z_EXPLORE_SCOUT_CHARS` | Total inject budget (default 2800) |
| `Z_EXPLORE_SCOUT_FILES` | Max files to peek (default 5) |

Deep scout for each top candidate (budgeted):
1. Keep hit snippets from rg / filename match
2. Peek file for `def` / `class` / `function` / `fn` signatures (capped)
3. Suggest sibling test paths if they exist on disk
4. Truncate the whole block to the char budget

Non-goals: second agent, LLM-based explore, mutating the tree, auto-`/add`.

## Acceptance

- Deep mode includes signatures for a matching source file.
- Thin mode restores the short path-only block.
- Prior explore + coding-quality tests green.
