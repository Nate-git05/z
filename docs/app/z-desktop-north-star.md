# Z Desktop — executive north star

**Decision (2026-07-21):** Z becomes an **application**, not a terminal-first product.
The shell is inspired by / built from [openai/codex](https://github.com/openai/codex) (Apache-2.0).
The editor experience is Z-native — not inline “prompt the model in the buffer.”
The **Z model router** is the only model path.

## Product pillars (keep — they already work)

1. **Uncertainty tree** — structured risk nodes; UI sorts primarily by risk (user can re-sort).
2. **Skills** — generator + library; see where skills are created and applied.
3. **Commit block** — visual surface for blocked commits / PRs / verify failures.
4. **MCP** — connect tools in-app (not only on the website).
5. **Workspaces** (later) — workspace groups as first-class app surfaces.

## Non-goals for the app

- Bring-your-own-key as a primary product path (dev escape only).
- Terminal as the main UX (CLI remains a power-user / CI surface).
- Website-only MCP / account chrome that the app must duplicate forever.

## V1 (now — this repo)

Ship **current Z agent features** with:

| Item | V1 |
|------|----|
| Model access | **Router only** — pick preferred routed model; escalate when needed |
| BYOK | Hidden unless `Z_ALLOW_BYOK=1` (legacy / CI escape) |
| Uncertainty / skills / commit block | Same engines as today (CLI + APIs) |
| Desktop app UI | Not required for V1 ship — north star only |

## V2+ (app)

| Surface | Behavior |
|---------|----------|
| Uncertainty | Linked-list / node graph, default sort = risk |
| Skills | Browse generation + application |
| Commit block | Blocked changes / PRs with reasons |
| MCP | Connect from app settings |
| Workspace groups | Multi-root / team workspaces |

**Codex OSS note (audit):** [openai/codex](https://github.com/openai/codex) is CLI/TUI +
`app-server` JSON-RPC — **not** a Tauri/desktop GUI. Local clone lives at
`apps/z-desktop/vendor/codex` (gitignored). Reuse protocol/sandbox/MCP *patterns*;
build the GUI (Tauri + Monaco) ourselves. Full plan:
[`z-editor-v1-implementation-plan.md`](./z-editor-v1-implementation-plan.md).

## Escapes

| Env | Effect |
|-----|--------|
| `Z_ALLOW_BYOK=1` | Restore BYOK vs router choice + BYOK setup |
| `Z_SKIP_ACCOUNT=1` | Existing account bypass for local/dev |
