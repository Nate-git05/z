# Z Desktop (app shell)

**Status:** Phase 0–1 implementation in progress (IPC + gateway + extension scaffold).

**Read first:** [`docs/app/z-editor-v1-implementation-plan.md`](../../docs/app/z-editor-v1-implementation-plan.md)

## Foundations (two upstreams)

| Upstream | Path (gitignored) | Role for Z |
|----------|-------------------|------------|
| [microsoft/vscode](https://github.com/microsoft/vscode) (MIT) | `apps/z-desktop/vendor/vscode` | **Editor shell** — workbench, file tree, tabs, Monaco, Electron |
| [openai/codex](https://github.com/openai/codex) (Apache-2.0) | `apps/z-desktop/vendor/codex` | **Agent protocol patterns** — app-server JSON-RPC, MCP, session lifecycle |

Codex OSS is **not** a desktop GUI (`codex app` installs a separate binary). VS Code OSS **is** the Cursor-shaped shell we build on.

## Layout (this repo)

| Path | Role |
|------|------|
| `protocol/ipc-v0.json` | JSON-RPC method sketch (Codex-shaped, Z payloads) |
| `product.z.json` | Brand overlay for Code - OSS `product.json` |
| `extension/` | Z activity-bar contribution (Chat / Uncertainty / Skills / Commit blocks / Profile / MCP) |
| `vendor/` | Local clones only — **gitignored**, never commit |

## Local IPC

```bash
# Requires: pip install websockets   (or aider-chat[web])
z app-server                  # ws://127.0.0.1:8741
# Override: Z_APP_SERVER_URL=ws://127.0.0.1:8741
```

Python package: `aider.z.app_server` — handlers for initialize, uncertainty/list, skills/*, commit_blocks/list, mcp/list, auth/status, turn/start (stub).

## Routing gateway

Cloud surface inside `z_server`:

- `POST /v1/gateway/chat/completions` — OpenAI-compatible; logs `gateway_requests`
- `GET /v1/gateway/usage` — live aggregates for Profile
- Server keys: `Z_GATEWAY_OPENAI_API_KEY` (dev stub when unset + `Z_SERVER_DEV=1`)

CLI router mode points litellm at the gateway via `aider.z.gateway_client` (`Z_USE_GATEWAY=0` to disable).

## Clone vendors locally

```bash
gh repo clone microsoft/vscode apps/z-desktop/vendor/vscode -- --depth 1
gh repo clone openai/codex apps/z-desktop/vendor/codex -- --depth 1
```

## Build order (next)

1. Apply `product.z.json` when building from `vendor/vscode`.
2. Bundle `extension/` as a built-in contribution; spawn `z app-server` on window open.
3. Phase 4: real `turn/start` → Coder + streaming notifications.
4. Phases 6–10: live webview panels (uncertainty chain, skills author, commit-blocks, profile, MCP).
