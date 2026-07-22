# Install Z Editor

You do **not** need to clone Seam to try the UI. Pick a path below.

**Pinned Seam SHA (full app builds):** `0514584208c175847d65abd907aa51937343cffb`  
**Code-OSS baseline:** 1.128.0 · **Node for vendor builds:** 24.17.0 (see `vendor/vscode/.nvmrc`)

---

## Path A — Extension VSIX (fastest)

1. **Install the Z engine** (Python 3.10+):

```bash
git clone https://github.com/Nate-git05/z.git
cd z
pip install -e ".[web]"   # includes websockets for app-server
which z
```

2. **Get the VSIX**
   - From CI: Actions → workflow **Z Extension VSIX** → artifact `z-editor-*.vsix`
   - Or build locally:

```bash
cd apps/z-desktop/extension
npm ci
npm run package    # produces z-*.vsix
```

3. **Install into VS Code or Cursor**  
   Extensions → `…` → **Install from VSIX…** → select the file.

4. Open a folder → **Z: Open Chat**.  
   If `z` is missing, the first-run wizard offers install help or **Locate z binary**.

---

## Path B — Branded Z Editor (unsigned Electron)

Requires a local Seam clone (or CI artifact when published).

```bash
# From repo root
gh repo clone Nate-git05/Seam apps/z-desktop/vendor/vscode -- --depth 1
cd apps/z-desktop/vendor/vscode && git checkout 0514584208c175847d65abd907aa51937343cffb

cd ../..   # apps/z-desktop
./scripts/package.sh                 # brand + inject extension
./scripts/package.sh vscode-linux-x64   # full gulp (long)
# or: ./scripts/package.sh vscode-darwin-arm64
```

Artifacts appear under `vendor/vscode` build output (e.g. `VSCode-linux-x64/` or `.build/`).

Still install the Z engine (Path A step 1) unless a future release bundles `z-runtime/`.

---

## Path C — Dev (no VSIX)

```bash
cd apps/z-desktop/extension
npm ci && npm run compile
# F5 "Run Z Extension" or symlink into ~/.vscode/extensions
```

---

## After install

| Step | Action |
|------|--------|
| Sign in | **Z: Sign in** (or `z-editor://signin` on branded builds) |
| Theme | **Z Terminal** (auto on activate) |
| MCP | Left **MCP** panel → GitHub PAT → Trust |
| Troubleshoot | **Z: Show connection status** / **Z: Install engine help** |

## Escapes

| Env / setting | Effect |
|---------------|--------|
| `z.zBinary` | Full path to `z` |
| `z.autoStartAppServer` | `false` to attach only |
| `Z_APP_SERVER_URL` | Override WebSocket URL |
| `z.promptInstallEngine` | `false` to silence first-run wizard |
