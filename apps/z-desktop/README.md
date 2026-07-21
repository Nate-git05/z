# Z Desktop (app shell)

**Status:** plan + local vendor clones — not a shipping binary yet.

**Read first:** [`docs/app/z-editor-v1-implementation-plan.md`](../../docs/app/z-editor-v1-implementation-plan.md)

## Foundations (two upstreams)

| Upstream | Path (gitignored) | Role for Z |
|----------|-------------------|------------|
| [microsoft/vscode](https://github.com/microsoft/vscode) (MIT) | `apps/z-desktop/vendor/vscode` | **Editor shell** — workbench, file tree, tabs, Monaco, Electron |
| [openai/codex](https://github.com/openai/codex) (Apache-2.0) | `apps/z-desktop/vendor/codex` | **Agent protocol patterns** — app-server JSON-RPC, MCP, session lifecycle |

Codex OSS is **not** a desktop GUI (`codex app` installs a separate binary). VS Code OSS **is** the Cursor-shaped shell we build on.

## Intent

Desktop app for Z, shaped like Cursor:

- Workspace-first coding (not terminal-primary)
- **Z model router only** (no BYOK as the product path)
- Visual **uncertainty tree** (risk-sorted nodes)
- **Skills** browser (generation + application)
- **Commit block** surface (blocked changes / PRs)
- **MCP** connections in-app

Full decision: [`docs/app/z-desktop-north-star.md`](../../docs/app/z-desktop-north-star.md)

## Clone locally

```bash
# From repo root — both trees are gitignored under apps/z-desktop/vendor/
gh repo clone microsoft/vscode apps/z-desktop/vendor/vscode -- --depth 1
gh repo clone openai/codex apps/z-desktop/vendor/codex -- --depth 1
```

## Next

1. Fork/brand VS Code OSS → Z Editor product identity (`product.json`, icons, name).
2. Add a Z workbench contribution (chat + uncertainty + skills + commit-block + MCP + profile).
3. Wire local `z-app-server` (Python) for agent IPC; model calls via routing gateway.
4. Keep Codex as read-only protocol reference — do not vendor either tree into git history.
