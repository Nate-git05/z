# Z Editor — Installable Desktop Packaging Plan

**Date:** 2026-07-22  
**Branch:** `cursor/z-editor-installable-impl-313a`  
**Status:** Implementing — P0–P5 landed (VSIX, icons, inject, CI, first-run); P6 signing deferred  
**Depends on:** Phases 0–15 on `cursor/z-editor-completion-impl-313a`  
**Pinned Seam SHA:** `0514584208c175847d65abd907aa51937343cffb`  
**Related:** [`z-editor-completion-plan.md`](./z-editor-completion-plan.md), [`z-editor-v1-implementation-plan.md`](./z-editor-v1-implementation-plan.md), [`apps/z-desktop/README.md`](../../apps/z-desktop/README.md)

---

## 0. What’s available for the program **now** (before installers)

This is the honest product surface today. Nothing below requires a Z Editor `.dmg` / App Store build.

### 0.1 You can already use

| Surface | How | What you get |
|---------|-----|----------------|
| **`z` CLI** | `pip install -e .` (or from git) then `z` | Sign-in, agent loop, skills, uncertainty, MCP list (cloud), routing via gateway |
| **`z app-server`** | `pip install websockets` then `z app-server --port 8741` | Local WebSocket IPC for the desktop UI |
| **Z extension UI** | Compile `apps/z-desktop/extension` → load in **VS Code or Cursor** (F5 / symlink / manual VSIX) | Agent-first Chat (center), Uncertainty / Skills / MCP / Profile (left), Commit Gate (right), Z Terminal theme |
| **MCP connect (local)** | MCP panel → GitHub PAT or custom | Stores under `~/.z/mcp/`; first-use trust; optional cloud sync |
| **MCP tools in turns** | Runtime + `` ```z-mcp `` `` fences (needs Node/`npx` for GitHub server) | Agent can call connected tools when handshake works |
| **OAuth MCP start** | Signed-in → `mcp/oauthStart` → browser | Needs `Z_MCP_GITHUB_*` on server; return deep-link best with branded `z-editor://` |
| **Profile usage** | Signed-in → live gateway aggregates | Empty (honest) when signed out |
| **Brand overlay scripts** | `./apps/z-desktop/scripts/apply-product.sh` against local Seam clone | Renames product.json → Z Editor (does **not** produce a downloadable binary by itself) |

### 0.2 You cannot do yet

| Missing | Why |
|---------|-----|
| Download “Z Editor” from a website | No CI artifacts / release channel |
| Double-click `.app` / `.exe` / `.deb` | Electron package not built/published |
| OS-level `z-editor://` without branded shell | Stock VS Code/Cursor uses its own URL protocol |
| Zero-setup MCP | Still needs Node/`npx` (and network) for stdio MCP servers |
| Bundled Python/`z` inside the app | Extension still spawns `z app-server` from **PATH** |

### 0.3 Runtime contract today (important)

```
[ VS Code / Cursor / future Z Editor ]
        │  WebSocket IPC
        ▼
[ z app-server ]  ← must be installed separately (pip)
        │
        ▼
[ Z agent + gateway + ~/.z/* ]
        │
        ▼
[ MCP stdio processes via npx/node ]  ← optional, machine-local
```

**Installable packaging must decide:** keep this split (Slice 1) or eventually **bundle** `z` + Python (+ optional Node) inside the Electron app (Slice 3).

### 0.4 Assets already in repo (packaging-relevant)

| Asset | Path | State |
|-------|------|--------|
| Product overlay | `apps/z-desktop/product.z.json` | Ready; `updateUrl` empty; Win AppIds placeholders |
| Apply / package scripts | `apps/z-desktop/scripts/apply-product.sh`, `package.sh` | Apply works; package.sh **prints** gulp steps only |
| Brand mark | `apps/z-desktop/brand/z-mark.svg` | SVG only — no icns/ico/png yet |
| Extension | `apps/z-desktop/extension` @ `0.8.0` | Compiles; **no vsce / VSIX CI** |
| Seam vendor (local) | `apps/z-desktop/vendor/vscode` (gitignored) | Present in some envs; Code-OSS **1.128.0**, npm, Node **24.17**, gulp `vscode-{linux,darwin,win32}-*` |
| Desktop CI | — | **None** |

---

## 1. Goals & non-goals

### Goals

1. Users can **install Z Editor** without cloning the monorepo (download an artifact).
2. First launch shows **Z** branding, Z Terminal theme, agent-first layout, Chat as center.
3. App-server starts reliably (bundled **or** clearly guided external `z` install).
4. `z-editor://` deep links work for sign-in and MCP OAuth return.
5. Repeatable CI produces **unsigned** then **signed** artifacts with a documented release process.

### Non-goals (this plan’s first ship)

- Replacing the marketing website / waitlist with desktop distribution as the only surface.
- Full Microsoft Marketplace publication of the extension (branded app can keep gallery empty).
- Shipping Windows signed MSI in Slice 1 (Linux + macOS first).
- Bundling every possible MCP server binary (keep `npx` unless we later pin a Node runtime).

---

## 2. Architecture for an installable app

### 2.1 Target end-state (Slice 2+)

```
┌──────────────────────── Z Editor.app / tarball ─────────────────────────┐
│  Electron (Seam / Code-OSS)                                             │
│   · product.json = Z Editor                                             │
│   · built-in extension: z-editor                                        │
│   · icons, urlProtocol=z-editor                                         │
│   · optional: embedded resources/z-runtime/  (python + z)               │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ spawn / attach
                                ▼
                     z app-server (localhost WS)
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
              Z auth / gateway         ~/.z (creds, mcp, skills)
```

### 2.2 Packaging layers

| Layer | Contents | Owner |
|-------|----------|-------|
| **Shell** | Electron + workbench | Seam / `vendor/vscode` |
| **Brand** | `product.z.json`, icons, About strings | `apps/z-desktop/` |
| **UI** | Built-in Z extension | `apps/z-desktop/extension/` |
| **Brain** | `z` + app-server | Python package (bundled or sibling install) |
| **Update** | `updateUrl` + release JSON | New infra |
| **Sign** | Apple/Windows certs | Org secrets |

---

## 3. Build order (Phases P0–P5)

```
P0 — Inventory freeze & pin Seam SHA
P1 — VSIX + one-path “use Z today” docs          ← ship value immediately
P2 — Icon set + product.json hardening
P3 — Built-in extension wiring + apply-product CI
P4 — Unsigned CI artifacts (Linux x64, macOS arm64)
P5 — Runtime bundling decision + first-run wizard
P6 — Signing, notarization, updateUrl, Windows     ← production
```

**Solo order:** P0 → P1 → P2 → P3 → P4 → P5 → P6.  
**Parallel:** P1 ∥ P2 after P0; P5 design can start during P4.

---

## 4. Phase P0 — Inventory freeze & pin Seam

### Work

1. Pin Seam commit SHA in `apps/z-desktop/README.md` and this plan (example observed: `0514584208c` on Code-OSS **1.128.0** — re-pin at implement time).
2. Record Node version (vendor `.nvmrc` → **24.17.0**), package manager (**npm**), gulp task names.
3. Document machine requirements (disk, RAM, time) for a full gulp package.
4. Fix doc drift: completion plan still says “no build scripts” in places — point to `scripts/`.

### Acceptance

- README has a single “Pinned Seam SHA” line.
- `apply-product.sh --check` documented as fail-before / pass-after.

---

## 5. Phase P1 — VSIX + “use Z today” (no Electron)

**Why first:** Users who “can’t download Z Editor” still get a one-click-ish UI install.

### Work

| Item | Detail |
|------|--------|
| Add `@vscode/vsce` | DevDep + `npm run package` → `z-editor-<ver>.vsix` |
| `.vscodeignore` | Exclude `src/`, tests, maps as appropriate |
| CI job | Compile + `vsce package`; upload artifact on tag/PR |
| Docs | `apps/z-desktop/INSTALL.md`: pip install `z[web]` → install VSIX → Open Chat |
| Launch config | Optional `.vscode/launch.json` for F5 |

### Acceptance

1. GitHub Actions artifact: `z-editor-0.8.x.vsix`
2. Fresh machine: install VSIX + `pip install '.[web]'` → Chat works against gateway when signed in

### Limitation (document clearly)

Stock VS Code/Cursor may not register `z-editor://`; sign-in via command palette remains primary.

---

## 6. Phase P2 — Icons & product hardening

### Work

1. Generate from `brand/z-mark.svg`:
   - Linux: `z-editor.png` sizes (16–512) → `vendor/vscode/resources/linux/`
   - macOS: `z-editor.icns` → `resources/darwin/`
   - Windows: `z-editor.ico` → `resources/win32/`
2. Extend `apply-product.sh` to **copy icons** after merge (idempotent).
3. Replace `{{ZEDITOR-*-PLACEHOLDER}}` Win AppIds with real GUIDs (generate once, commit).
4. Revisit dangerous overlay defaults:
   - `builtInExtensions: []` currently **wipes** upstream built-ins on merge — change to explicit Z-only list **or** merge-append strategy.
   - `defaultChatAgent` still Copilot-oriented in Seam — neutralize or point at Z Chat command.
5. Keep `extensionsGallery` empty for V1 branded app (policy lock) **or** document why.

### Files

- `apps/z-desktop/brand/**`
- `apps/z-desktop/scripts/apply-product.sh`
- `apps/z-desktop/product.z.json`
- Optional: `apps/z-desktop/scripts/generate-icons.sh` (svgexport / iconutil / ImageMagick)

### Acceptance

- Packaged About / dock / `.desktop` icon shows Z mark, not Code-OSS.
- `apply-product.sh --check` validates icon files exist when `--require-icons`.

---

## 7. Phase P3 — Built-in extension + CI apply

### Work

1. **Wire Z as built-in:**
   - **Preferred:** copy compiled extension into `vendor/vscode/extensions/z-editor/` and add to gulp extension compile list / package.json of that extension.
   - **Alt:** `builtInExtensions` entry with local `vsix` path (see `build/lib/builtInExtensions.ts`).
2. On first run (product defaults):
   - Color theme = Z Terminal
   - Open Chat (`z.openChatOnActivate`)
   - Show left `z-left` + right `z-right`
   - Suppress noisy VS Code walkthrough where product.json allows
3. CI workflow `.github/workflows/z-desktop-brand.yml`:
   - Checkout / cache Seam at pinned SHA
   - `apply-product.sh`
   - Compile extension + inject built-in
   - `apply-product.sh --check`
   - **Do not** full gulp yet (that’s P4) — keep this job under ~minutes

### Acceptance

- Branded tree contains Z extension under `extensions/z-editor`.
- Fresh branded build opens Chat without “Install extension from VSIX”.

---

## 8. Phase P4 — Unsigned CI artifacts (the first “download”)

### Targets (Slice 1)

| Platform | Gulp task (Seam) | Artifact |
|----------|------------------|----------|
| Linux x64 | `vscode-linux-x64` | `Z-Editor-linux-x64.tar.gz` (rename from Code-OSS layout) |
| macOS arm64 | `vscode-darwin-arm64` | `Z-Editor-darwin-arm64.zip` / `.app` |

Defer: Windows, Linux arm64, macOS Intel (add when CI runners exist).

### Workflow sketch

`.github/workflows/z-desktop-package.yml`:

1. Runner with Node 24 + enough disk  
2. Clone Seam @ pin → `apps/z-desktop/vendor/vscode`  
3. `apply-product.sh` + icons + built-in extension  
4. `npm ci` in vendor (cache npm)  
5. `npm run gulp -- vscode-linux-x64` (matrix)  
6. Rename/brand artifact filenames  
7. Upload GitHub Actions artifact + optional GitHub Release on tag `z-editor-v*`

### Smoke (automated where possible)

| Check | How |
|-------|-----|
| Binary exists / `--version` or help | CLI of packaged app |
| `product.json` nameLong | Unpack & jq |
| Extension present | Path under `resources/app/extensions/z-editor` |
| Manual | Launch → Chat → sign-in (human checklist in README) |

### Runtime expectation for Slice 1 (explicit)

**App does not yet bundle Python.** First-run shows a Z welcome page:

- If `z` missing: “Install Z engine: `pip install 'z-chat[web]'` …” (final package name TBD) + link  
- “Locate binary” picker → writes `z.zBinary`  
- Then spawn app-server  

This matches current `spawnCommand: ["z","app-server"]` without boiling the ocean.

### Acceptance

- Public download link on a GitHub Release (unsigned).
- README “Download” section with platform table + engine install note.

---

## 9. Phase P5 — Runtime bundling & first-run

### Decision gate (choose one before coding)

| Option | Pros | Cons |
|--------|------|------|
| **A. Sidecar (Slice 1 default)** | Small app; reuse pip updates | Two installers; PATH pain |
| **B. Embed venv** | One download feels complete | Size; update story; signing |
| **C. Embed PyInstaller `z-app-server` only** | Enough for editor; CLI optional | Still need model/gateway deps inside binary |

**Plan recommendation:** Ship **P4 with A**, then **P5-B or P5-C** as soon as unsigned downloads exist — otherwise “installable editor” still feels broken.

### P5 work (if B/C)

1. Build pipeline produces `z-runtime/` (python + site-packages + `z` entry).
2. Extension resolves binary:  
   `context.extensionPath/../z-runtime/bin/z` → else `z.zBinary` → else PATH.
3. First-run wizard only if runtime missing/corrupt.
4. Optional: ship Node LTS subset for MCP `npx` (or document “install Node 20+”).

### Acceptance

- Clean machine, **only** Z Editor download (no pip), can open Chat and complete a gateway turn when signed in.
- MCP GitHub still may require Node unless bundled.

---

## 10. Phase P6 — Signed releases & updates

### Work

1. **macOS:** Developer ID sign + notarize; staple; distribute `.dmg` or `.zip`.
2. **Windows:** Authenticode; real AppIds; MSIX or setup exe (use Seam win32 targets).
3. **Linux:** `.tar.gz` + optional `.deb` from `resources/linux/debian` templates (rebrand).
4. Set `product.z.json` `updateUrl` to a hosted update feed compatible with VS Code’s update protocol.
5. Channels: `quality: stable` (+ later `insider`).
6. Secrets in GitHub Environments; never in git.
7. Release checklist + SBOM / license notice (Code-OSS MIT + Z MIT + third-party).

### Acceptance

- Gatekeeper / SmartScreen clean on smoke machines.
- In-app update check hits `updateUrl` (even if “up to date” only).

---

## 11. Protocol, deep links, and OAuth

| Concern | Installable requirement |
|---------|-------------------------|
| `urlProtocol: z-editor` | Must be in packaged `product.json` (apply-product) |
| Sign-in | `z-editor://signin`, `z-editor://auth/complete` |
| MCP OAuth | Server redirects to `z-editor://mcp/oauth/done` when `client=z-editor` (already implemented) |
| VSIX-only hosts | Document fallbacks (command palette) |

---

## 12. Security & size

| Topic | Policy |
|-------|--------|
| Auto-update | HTTPS only; signed packages in P6 |
| Extensions gallery | Empty in V1 branded app (no random marketplace malware surface) |
| Bundled secrets | None — user signs in; MCP secrets in `~/.z/mcp/secrets` mode 0600 |
| Telemetry | Follow product decision (VS Code telemetry flags in product.json — audit before ship) |
| Artifact size | Expect 100MB–300MB+ Electron; document; strip maps in production |

---

## 13. CI matrix (target)

| Job | Trigger | Output |
|-----|---------|--------|
| `z-extension-vsix` | PR + tag | `.vsix` |
| `z-desktop-brand-check` | PR | apply-product + inject extension (no full gulp) |
| `z-desktop-package-linux` | tag / workflow_dispatch | `.tar.gz` |
| `z-desktop-package-darwin` | tag / workflow_dispatch (macos runner) | `.app` zip |
| `z-desktop-sign` | release | signed artifacts (P6) |

---

## 14. File map (to create/edit when implementing)

| Path | Phase |
|------|-------|
| `docs/app/z-editor-installable-plan.md` | this doc |
| `apps/z-desktop/INSTALL.md` | P1 |
| `apps/z-desktop/extension` vsce scripts + `.vscodeignore` | P1 |
| `.github/workflows/z-extension-vsix.yml` | P1 |
| `apps/z-desktop/brand/**` icons | P2 |
| `apps/z-desktop/scripts/apply-product.sh` (icons) | P2 |
| `apps/z-desktop/scripts/inject-builtin-extension.sh` | P3 |
| `apps/z-desktop/product.z.json` (builtIns, AppIds) | P2–P3 |
| `.github/workflows/z-desktop-package.yml` | P4 |
| `apps/z-desktop/scripts/package.sh` (actually invoke gulp) | P4 |
| Runtime bundle scripts | P5 |
| Signing workflows | P6 |

---

## 15. Tests & acceptance checklist

### Automated

- Extension `tsc` + vsce package succeeds  
- `apply-product.sh --check` after apply  
- Artifact contains `extensions/z-editor` and branded `product.json`  
- Python unit tests unchanged green  

### Manual E2E (each unsigned build)

1. Install / unpack → launch → title **Z Editor**  
2. Chat opens; theme Z Terminal  
3. Sign in (deep link or command)  
4. Open folder → turn completes via gateway  
5. MCP connect PAT → Test → Trust → tool row in Chat (if Node present)  
6. Quit / relaunch restores session  

---

## 16. Cut lines

If schedule slips, cut in this order:

1. Windows (P6)  
2. Auto-update (ship static releases first)  
3. Embedded Node for MCP  
4. Embedded Python (keep sidecar + excellent first-run)  
5. **Never cut:** VSIX (P1), branded unsigned Linux or macOS artifact (P4), `z-editor://` in product.json, first-run engine guidance  

---

## 17. Suggested PR slicing

| PR | Scope |
|----|--------|
| 1 | P0 pin + INSTALL.md + VSIX pipeline (P1) |
| 2 | Icons + product hardening (P2) |
| 3 | Built-in inject scripts (P3) |
| 4 | Package workflow Linux (P4) |
| 5 | Package workflow macOS (P4) |
| 6 | Runtime bundle (P5) |
| 7 | Signing + updateUrl (P6) |

Do not mix full gulp packaging with MCP runtime changes in the same PR.

---

## 18. Open questions (resolve at implement time)

1. Public package name on PyPI for the engine (`z-chat` vs `aider-chat` vs `z`)?  
2. Do we strip Copilot from Seam builds entirely for Z releases?  
3. Update hosting: GitHub Releases only vs dedicated `updateUrl` CDN?  
4. Minimum macOS / glibc versions to support?  
5. Should the welcome hero follow marketing brand rules (full-bleed) inside workbench webview?

---

## 19. Success statement

**Done (Slice 1):** A user who has never cloned this repo can open a GitHub Release, download **Z Editor** for their OS, install the Z engine when prompted (or find it bundled in Slice 2), sign in, and chat with the agent in a window that says **Z** — without installing the Microsoft VS Code marketplace extension by hand.

**Done (Slice 2):** Same flow with **no separate pip step** and signed updates.

---

## 20. Implementation checklist

```
[x] P0 Pin Seam SHA + refresh README packaging section
[x] P1 vsce + CI VSIX + INSTALL.md
[x] P2 Icons + AppIds + builtInExtensions merge strategy
[x] P3 Inject built-in Z extension + brand-check CI
[x] P4 Linux/macOS package workflows (workflow_dispatch / tags)
[x] P5 First-run wizard + bundled z-runtime path resolution
[ ] P6 Sign / notarize / updateUrl
[x] Update z-desktop README Download section
```

### Implementation map (landed)

| Piece | Path |
|-------|------|
| Icons | `apps/z-desktop/brand/`, `scripts/generate-icons.py` |
| Apply + icons | `scripts/apply-product.sh` |
| Built-in inject | `scripts/inject-builtin-extension.sh` |
| Package | `scripts/package.sh [gulp-target]` |
| VSIX | `extension` `npm run package` + `.github/workflows/z-extension-vsix.yml` |
| Brand CI | `.github/workflows/z-desktop-brand.yml` |
| Electron CI | `.github/workflows/z-desktop-package.yml` |
| First-run | `extension/src/firstRun.ts` |
| User doc | `apps/z-desktop/INSTALL.md` |
