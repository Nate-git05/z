# Z Editor extension (Phase 4)

VS Code contribution that ships inside the branded Code - OSS fork.

## Behavior

- **Chat (Phase 4):** type prompts in the Chat sidebar → `turn/start` → stream + WaitingInput (plan/shell confirms)
- **Spawn/attach:** probes `z.appServerUrl`, spawns `z app-server` with `--pid-file` when needed, connects + `initialize`
- **Workspace sync:** `workspace/open` when the VS Code folder changes (tabs/save stay native)
- **Auth:** Profile webview Sign in/out; commands `Z: Sign in`; URI handler `z-editor://signin?method=google`

## Develop against stock VS Code / Cursor

```bash
cd apps/z-desktop/extension
npm install
npm run compile
# F5 → Run Extension, or symlink into .vscode/extensions
```

Requires `z` on PATH (or set `z.zBinary`) and `websockets` installed for the Python app-server.
