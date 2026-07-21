# Z Desktop (app shell)

**Status:** Phase 0–3 — IPC, gateway, extension lifecycle + auth UX.

**Read first:** [`docs/app/z-editor-v1-implementation-plan.md`](../../docs/app/z-editor-v1-implementation-plan.md)

## Foundations (two upstreams)

| Upstream / our fork | Path (gitignored) | Role for Z |
|---------------------|-------------------|------------|
| [microsoft/vscode](https://github.com/microsoft/vscode) → fork [Nate-git05/Seam](https://github.com/Nate-git05/Seam) | `apps/z-desktop/vendor/vscode` | **Editor shell** — workbench, file tree, tabs, Monaco, Electron |
| [openai/codex](https://github.com/openai/codex) → fork [Nate-git05/codex](https://github.com/Nate-git05/codex) | `apps/z-desktop/vendor/codex` | **Agent protocol patterns** — app-server JSON-RPC, MCP, session lifecycle |

## Layout

| Path | Role |
|------|------|
| `protocol/ipc-v0.json` | JSON-RPC methods + deep-link sketch |
| `product.z.json` | Brand overlay for Code - OSS `product.json` |
| `extension/` | Z activity-bar contribution + lifecycle manager |
| `vendor/` | Local clones only — **gitignored** |

## Clone vendors (your forks)

```bash
# From repo root — trees stay gitignored under apps/z-desktop/vendor/
rm -rf apps/z-desktop/vendor/vscode apps/z-desktop/vendor/codex
gh repo clone Nate-git05/Seam apps/z-desktop/vendor/vscode -- --depth 1
gh repo clone Nate-git05/codex apps/z-desktop/vendor/codex -- --depth 1
# remotes: origin = your fork, upstream = microsoft/vscode | openai/codex
```

## Phase 3 — shell lifecycle

| Piece | Behavior |
|-------|----------|
| **3a Folder / tabs / save** | Inherited from Code - OSS. Extension syncs the open folder via `workspace/open` when folders change. |
| **3b Spawn / attach** | On activate (if `z.autoStartAppServer`), probe `ws://127.0.0.1:8741`; if down, spawn `z app-server --pid-file ~/.z/app-server/<port>.pid`, wait until reachable, then `initialize`. |
| **3c Auth** | Profile webview + `Z: Sign in` / deep links `z-editor://signin?method=google\|z`. Browser login via `auth/loginStart` → `vscode.env.openExternal` → poll `auth/loginStatus`. |

```bash
pip install websockets   # or aider-chat[web]
z app-server --host 127.0.0.1 --port 8741 --pid-file ~/.z/app-server/8741.pid
```

Extension settings: `z.appServerUrl`, `z.autoStartAppServer`, `z.zBinary`.

## Routing gateway

- `POST /v1/gateway/chat/completions`
- `GET /v1/gateway/usage`
- Router CLI mode: `aider.z.gateway_client` (`Z_USE_GATEWAY=0` to disable)

## Next (Phase 4+)

1. Real `turn/start` → Coder + Busy/WaitingInput stream into Chat panel
2. Live uncertainty / skills / commit-blocks / MCP webviews
3. Apply `product.z.json` when building from `vendor/vscode`
