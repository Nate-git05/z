# Z Editor extension (Phase 0)

VS Code contribution that will ship inside the branded Code - OSS fork.

## What this is

- Activity bar container **Z** with six webview placeholders: Chat, Uncertainty, Skills, Commit blocks, Profile, MCP
- Commands to spawn / reconnect `z app-server` (`ws://127.0.0.1:8741`)
- Thin JSON-RPC client (`src/appServerClient.ts`) matching `apps/z-desktop/protocol/ipc-v0.json`

## Develop against stock VS Code / Cursor

```bash
cd apps/z-desktop/extension
npm install
npm run compile
# Then: Run Extension (F5) from this folder, or symlink into .vscode/extensions
```

## Production path

Load this extension as a built-in (or bundled) contribution when building from `vendor/vscode` with `product.z.json` branding applied. See `apps/z-desktop/README.md`.
