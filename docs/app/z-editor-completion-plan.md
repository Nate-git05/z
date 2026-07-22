# Z Editor — Completion Plan (post Phases 0–10)

**Date:** 2026-07-22  
**Branch:** `cursor/z-editor-completion-plan-313a`  
**Status:** Plan only — not yet implemented  
**Depends on:** Phases 0–10 on `cursor/z-editor-v1-impl-313a` (agent-first UI, gateway, uncertainty, skills, commit gate, Profile usage proxy, MCP connect/list/first-use store)  
**Look:** Z Terminal palette — near-black `#0A0A0A`, burnt orange `#C96A2B` / `#E07830`, text `#F5F5F5`  
**Related:** [`z-editor-v1-implementation-plan.md`](./z-editor-v1-implementation-plan.md), [`z-editor-phase-9-10-plan.md`](./z-editor-phase-9-10-plan.md), [`z-desktop-north-star.md`](./z-desktop-north-star.md)

---

## 1. Why this plan exists

Phases 0–10 shipped the **agent-first chrome** and the **management surfaces** (Profile charts, MCP connect panel). Five product gaps still block “Z Editor feels like a finished app”:

| # | Gap | User-visible failure |
|---|-----|----------------------|
| **A** | MCP tool runtime | “Connected GitHub” does nothing in Chat — no tool calls |
| **B** | Branded desktop shell | Still a VS Code extension, not a Z-named Electron app |
| **C** | OAuth MCP in-app | Linear/GitHub OAuth bounce to web; desktop can’t finish the loop |
| **D** | Live usage truth | Unsigned / offline shows demo numbers; cost may be null in logs |
| **E** | Polish + packaging | Thin Chat UX, weak errors, no installable build |

This document is the **build order, contracts, file map, tests, and cut lines** for closing A–E as **Phases 11–15**.

---

## 2. Current state (honest baseline)

### What already works

| Surface | Reality today |
|---------|----------------|
| Center Chat | `turn/start` → worker `Coder` → deltas / waiting_input / queue preview |
| Left | Uncertainty (live), Skills (CRUD draft), MCP (catalog/connect/test/trust), Profile (usage UI) |
| Right | Commit Gate list / two-step override / resolve |
| Gateway | Router-only completions + `GET /v1/gateway/usage` |
| MCP store | `~/.z/mcp/{connections,secrets,first_use}.json` + optional cloud sync |
| First-use helper | `AppServerIO.confirm_mcp_first_use` exists — **not called from a tool runner yet** |
| Brand tokens | Extension theme `Z Terminal` + webview CSS variables |
| Brand overlay file | `apps/z-desktop/product.z.json` (not applied in a build pipeline) |

### Critical technical findings

1. **`coder.mcp_tools` is load-only.** `aider/main.py` assigns `coder.mcp_tools = load_mcp_tools_for_session(...)`, but nothing in `aider/coders/` invokes MCP. Desktop turns via `turn_runner.py` do not load local MCP either.
2. **Local GitHub ≠ cloud GitHub.** `mcp_local` catalogs GitHub as **manual PAT**; `z_server.services.mcp_catalog` catalogs GitHub as **OAuth**. Completion work must reconcile: OAuth primary, PAT fallback.
3. **“Test connection” is soft.** For stdio it checks `npx`/`node` on PATH — it does **not** speak MCP JSON-RPC. Phase 11 must add a real handshake probe.
4. **No `apps/z-desktop` build scripts.** Vendors are gitignored; there is no documented `apply-product` / package step in-repo.
5. **Usage demo stub masks emptiness.** Profile always shows bars when stub/demo fires; signed-out should show empty + CTA, not fake spend.

---

## 3. Goals & non-goals

### Goals (ship)

1. Agent can **list and call** tools from connected MCP servers during a Chat turn (GitHub first).
2. First-use (D9) blocks the first tool call until the user trusts the server/tool in Chat or MCP panel.
3. User can finish **OAuth MCP** (GitHub, later Linear) inside Z Editor via `z-editor://` deep link (or loopback) without relying on the marketing site as the only UI.
4. A **branded Z Editor** binary/app can be built from Seam (`vendor/vscode`) with `product.z.json` applied.
5. Profile shows **true** gateway aggregates when signed in; clear empty/error states otherwise.
6. Chat feels denser and more reliable: streaming continuity, structured errors, reconnect, tool-call visibility.

### Non-goals (explicitly later)

- Full marketplace of MCP servers / arbitrary remote plugin install.
- Multi-root team workspaces (north-star V2).
- Replacing Aider’s edit model with a pure tool-calling agent.
- Shipping Windows/macOS signed notarized installers in the first packaging slice (unsigned local builds OK for Phase 12).
- Implementing every catalog server equally — **GitHub stdio is the Phase 11 acceptance target**; others follow the same runtime.

---

## 4. Architecture overview

```
┌──────────────── Z Editor (Seam / extension) ─────────────────┐
│  Chat  │  Uncertainty │ Skills │ MCP │ Profile │ Commit Gate │
└───────────────┬───────────────────────────────┬──────────────┘
                │ IPC (ipc-v0)                  │ deep links
┌───────────────▼───────────────────────────────▼──────────────┐
│                     z-app-server                              │
│  turn_runner ──► AppServerIO ──► Coder                        │
│       │                                                       │
│       ├─► mcp_runtime.SessionManager  (NEW Phase 11)          │
│       │      spawn stdio / connect SSE                        │
│       │      tools/list · tools/call                          │
│       │      first-use gate via AppServerIO                   │
│       │                                                       │
│       ├─► mcp_local (connections + secrets)                   │
│       └─► usage_client → GET /v1/gateway/usage                │
└───────────────┬───────────────────────────┬───────────────────┘
                │ optional sync             │ OAuth start/callback
                ▼                           ▼
         z_server /v1/mcp/*          provider authorize URLs
```

**Design lock — local-first runtime:** tool processes spawn on the user’s machine from `~/.z/mcp` config. Cloud holds account-linked credentials for sync/OAuth; it does **not** proxy tool calls in V1 completion.

---

## 5. Phase map (build order)

```
Phase 11 — MCP tool runtime          ← blocks “GitHub works”
  11a. SessionManager (stdio + SSE)
  11b. Wire into turn_runner / Coder tool surface
  11c. First-use on tools/call + Chat tool cards
  11d. Real handshake test; GitHub acceptance

Phase 12 — Branded desktop shell
  12a. apply-product.z.json script + icons
  12b. Built-in Z extension + default layout
  12c. Local package (macOS/Linux first)
  12d. Smoke: launch → app-server → Chat

Phase 13 — OAuth MCP in-app
  13a. Align catalog (GitHub OAuth + PAT fallback)
  13b. mcp/oauthStart + deep-link callback
  13c. Persist tokens → local store → runtime
  13d. Linear (or second OAuth) once GitHub green

Phase 14 — Live usage truth
  14a. Kill misleading demo when unsigned-in
  14b. Ensure cost_usd + tokens always logged on gateway path
  14c. Profile empty/error/live states + refresh on auth change
  14d. Optional: per-day sparkline from gateway_requests

Phase 15 — Polish + packaging
  15a. Chat density (tool rows, sticky composer, error taxonomy)
  15b. Streaming/reconnect hardening
  15c. Product packaging docs + CI artifact
  15d. End-to-end acceptance checklist
```

**Dependency rules**

- **11 before 13’s “OAuth GitHub usable in Chat”** — OAuth without runtime is still a dead connect.
- **12 can parallelize with 11** after IPC contracts for tool notifications are sketched (12 does not need tools/call).
- **14 can parallelize anytime** after Phase 9 (already landed); prefer after auth polish in 15b if staffing is thin.
- **15 last** — polish against real runtime + branded shell, not stubs.

**Staffing note:** If only one stream, order is **11 → 13 (GitHub OAuth) → 14 → 12 → 15**. If two streams: **(11∥12) → 13 → (14∥15a) → 15c–d**.

---

## 6. Phase 11 — MCP tool runtime

### 11a. SessionManager

**New module:** `aider/z/mcp_runtime.py` (name flexible; keep out of `mcp_local.py`).

Responsibilities:

| API | Behavior |
|-----|----------|
| `ensure_session(connection_id) → Session` | Spawn stdio (`command` + `args` + env from secrets) or open SSE/HTTP |
| `list_tools(connection_id) → ToolDesc[]` | MCP `tools/list` |
| `call_tool(connection_id, name, arguments) → CallResult` | MCP `tools/call` with timeout + cancel |
| `drop_session(connection_id)` | On disconnect / disable |
| `probe(connection_id) → {ok, tools_count, error}` | Real handshake for `mcp/test` |

Constraints:

- One session per connection; lazy start on first list/call.
- Env injection: map secrets (`token` → `GITHUB_PERSONAL_ACCESS_TOKEN` for GitHub server, etc.) via a small per-server env map in catalog.
- Hard timeouts (default 60s call, 15s probe); kill process on hang.
- Never return secrets over IPC.
- Log tool name + duration to `turn/log` (info); failures as warning/error.

**Stdio protocol:** JSON-RPC over stdin/stdout (MCP). Prefer an existing small dependency if already in tree; otherwise a minimal client (~initialize, tools/list, tools/call). Do **not** vendor a full MCP SDK unless necessary.

**SSE/HTTP:** Support custom URL transports after stdio GitHub is green (Phase 11d acceptance does not require SSE).

### 11b. Wire into turns / Coder

**Problem:** tools are never offered to the model.

**Approach (pragmatic V1):**

1. On `ThreadTurnRunner` coder init (and on MCP connect/disconnect notifications), build a **tool index** from all enabled local (+ synced cloud) connections via SessionManager.
2. Inject a compact tool catalog into the turn context (system/tool section), e.g.:
   ```
   Available MCP tools:
   - github.list_issues {repo}
   - github.create_issue {repo,title,body}
   ```
3. Add a **structured action channel** the model can emit that the runner intercepts — preferred options in order:
   - **A (best):** If current Coder/litellm path already supports function/tool calls, register MCP tools as functions and dispatch.
   - **B (fallback):** Fence protocol in assistant output, e.g. `<<<Z_MCP_CALL\n{"server","tool","arguments"}\n>>>`, parsed by turn_runner before finalizing the turn; execute; append tool result; continue the turn (single-depth loop, max N calls per turn).

**Lock for plan:** implement **B first** if function-calling integration is invasive; upgrade to **A** in a follow-up once B proves GitHub E2E. Document which path shipped in the PR.

Also:

- Load **local** runtime payload (`mcp_local.runtime_tools_payload()`) in app-server turns, not only cloud `fetch_mcp_runtime`.
- CLI `z` path should reuse SessionManager so terminal and editor share behavior.

### 11c. First-use + Chat visibility

On every `call_tool`:

1. `io.confirm_mcp_first_use(server_name, tool_name)` — blocks with `turn/waiting_input` kind `mcp_tool` if needed.
2. Emit notifications for the Chat UI:

| Notification | Payload |
|--------------|---------|
| `mcp/tool_started` | `{turnId, serverName, toolName, callId}` |
| `mcp/tool_finished` | `{turnId, callId, ok, summary, durationMs}` |
| `mcp/tool_error` | `{turnId, callId, error}` |

Chat renders a compact **tool row** (not a card farm): `github · list_issues · 1.2s` under the assistant stream. No hero overlays; Z Terminal colors.

MCP panel “Trust this server” remains the pre-approve path (`toolName: "*"`).

### 11d. GitHub acceptance criteria

Must pass manually + in automated smoke where possible:

1. Connect GitHub with PAT in MCP panel → status `connected`.
2. `mcp/test` performs initialize + tools/list → `ok: true`, `tools_count > 0` (requires network + `npx`).
3. In Chat: “List open issues in OWNER/REPO” → model triggers tool → first-use prompt once → issues text appears in transcript.
4. Disconnect kills session; subsequent call fails closed with a clear Chat error.
5. Secrets never appear in `mcp/list`, logs, or Chat.

**Escape:** `Z_MCP_RUNTIME=0` disables spawn (management UI still works).

### 11 — File map

| Piece | Path |
|-------|------|
| Runtime | `aider/z/mcp_runtime.py` |
| Env maps / catalog align | `aider/z/mcp_local.py` |
| Turn wire | `aider/z/app_server/turn_runner.py` |
| First-use (exists) | `aider/z/app_server/io_bridge.py` |
| IPC handlers | `mcp/test` upgrade; optional `mcp/tools` list |
| Protocol | `apps/z-desktop/protocol/ipc-v0.json` |
| Chat UI | `apps/z-desktop/extension/src/chatPanel.ts` |
| Tests | `tests/basic/test_z_mcp_runtime.py` (fake stdio server) |

### 11 — Tests

| Case | Notes |
|------|-------|
| Fake MCP server tools/list + tools/call | subprocess fixture |
| First-use blocks then allows | IO mock |
| `mcp/test` fails when command missing | unit |
| Turn loop executes one fenced call | turn_runner unit |
| Public list strips secrets | regression |

---

## 7. Phase 12 — Branded desktop shell

### 12a. Apply `product.z.json`

**Script:** `apps/z-desktop/scripts/apply-product.sh` (or `.mjs`)

1. Require `apps/z-desktop/vendor/vscode` (Seam clone).
2. Deep-merge `product.z.json` into `product.json` (preserve upstream keys not overridden).
3. Replace app icons from `apps/z-desktop/brand/` (add SVG/PNG/ICNS/ICO set — create minimal Z mark if missing).
4. Set `urlProtocol` to `z-editor` (already in overlay).
5. Idempotent + `--check` mode for CI.

Document in `apps/z-desktop/README.md`: clone Seam → apply-product → build.

### 12b. Built-in extension + default layout

| Work | Detail |
|------|--------|
| Bundle extension | Copy/compile `apps/z-desktop/extension` into Seam’s built-in extensions path **or** ship as the only recommended extension with auto-install on first launch |
| Default layout | On first run: open Chat center; show left `z-left` + right `z-right`; hide noisy VS Code walkthroughs where product.json allows |
| Welcome | Single Z-branded welcome: brand name hero-level, one line, one CTA (“Open folder” / “Sign in”) — follow frontend brand rules; no stat strips |
| Theme | Default color theme = **Z Terminal** |

### 12c. Local package

| Target | First slice |
|--------|-------------|
| Linux | `.tar.gz` or directory build from Code - OSS scripts |
| macOS | unsigned `.app` for dev |
| Windows | defer unless trivial |

Add `apps/z-desktop/scripts/package.sh` wrapping upstream gulp/npm package targets after apply-product.

### 12d. Smoke checklist

1. App title / About shows **Z Editor**.
2. `z-editor://` protocol registered (platform-dependent).
3. Extension activates; app-server spawns; Chat opens.
4. No “Visual Studio Code” primary branding in window title.

### 12 — Risks

- Seam drift vs microsoft/vscode build instructions — pin a Seam commit SHA in README.
- Built-in extension path differs by VS Code version — detect and fail with instructions.
- Icons/licensing — keep original assets in `apps/z-desktop/brand/`.

---

## 8. Phase 13 — OAuth MCP in-app

### 13a. Catalog alignment

| Server | Desktop catalog | Notes |
|--------|-----------------|-------|
| GitHub | **OAuth primary** + “Use PAT instead” advanced | Match `z_server` oauth metadata; keep PAT path for CI |
| Linear | OAuth | Already sketched locally as oauth deep-link |
| filesystem / custom | Manual | Unchanged |

Share a single catalog source if feasible: import `z_server.services.mcp_catalog` when available, else keep mirrored `DEFAULT_CATALOG` with a sync comment + test that key server names match.

### 13b. Desktop OAuth flow

```
MCP panel → mcp/oauthStart {serverName}
  → app-server calls GET /v1/mcp/oauth/start (auth’d)
  → returns authorizeUrl
  → extension openExternal(authorizeUrl)
  → provider redirects to z_server /v1/mcp/oauth/callback
  → z_server stores cloud connection
  → redirect to z-editor://mcp/oauth/done?server=github&status=ok
  → extension handles URI → mcp/sync or mcp/list refresh → local cache of non-secret metadata
```

**Token handling:** Prefer cloud as source of truth for OAuth refresh tokens; desktop runtime fetches `/v1/mcp/runtime` (already decrypts for CLI) and materializes an ephemeral local session env — **do not** write OAuth refresh tokens into `secrets.json` unless offline-required. PAT path continues to use local secrets.

### 13c. IPC additions

| Method | Result |
|--------|--------|
| `mcp/oauthStart` | `{ authorizeUrl, state, serverName }` |
| (URI) `z-editor://mcp/oauth/done` | triggers refresh |

Update `ipc-v0.json` + `authCommands`/URI router beside existing `z-editor://signin`.

### 13d. Acceptance

1. Click Connect on GitHub (OAuth) → browser → back to app → connection listed `source: cloud` or `local+cloud`.
2. Phase 11 runtime can call GitHub tools using runtime credentials.
3. Cancel / deny shows error in MCP panel, not a silent spinner.
4. Linear follows same path once GitHub OAuth green (same code, different catalog entry).

**Server env deps:** `Z_MCP_GITHUB_CLIENT_ID` / `Z_MCP_GITHUB_CLIENT_SECRET` (already referenced in catalog). Document in README; without them, UI shows “OAuth not configured — use PAT”.

---

## 9. Phase 14 — Live usage truth

### 14a. Stop lying with demo data

Change `usage_client.fetch_usage_summary`:

| Condition | Response |
|-----------|----------|
| Not signed in | `{ authenticated:false, byModel:[], totalRequests:0, totalCostUsd:0, note:"Sign in to see usage" }` — **no stub bars** |
| Signed in + gateway OK | Live SQL aggregate |
| Signed in + gateway error | `{ authenticated:true, error:"…", byModel:[] }` — UI error, not demo |
| `Z_GATEWAY_USAGE_STUB` set | Stub allowed (tests/dev only) |

Remove treating `Z_GATEWAY_STUB` as usage stub unless explicitly documenting that escape (prefer dedicated env only).

### 14b. Metering integrity

Audit `gateway_proxy` / completion path:

- Always persist `input_tokens`, `output_tokens`, `cost_usd` (estimate from model price table if provider omits cost).
- Ensure app-server / CLI router path hits the same logging as HTTP gateway clients.
- Add a unit/integration test: mocked completion → row in `gateway_requests` → usage summary non-zero.

### 14c. Profile UX states

| State | UI |
|-------|-----|
| Signed out | CTA Sign in; empty usage |
| Signed in, zero traffic | “No gateway requests this period” |
| Live data | Existing bars + table |
| Error | Inline error + Retry |

Refresh Profile automatically on `auth/loginStatus` succeeded and on connection restore.

### 14d. Optional sparkline

If cheap: include `days[]` from gateway (group by UTC day) for a CSS sparkline under totals. Skip if it threatens Phase 14 schedule — table/bars already satisfy Phase 9 intent.

---

## 10. Phase 15 — Polish + packaging

### 15a. Chat density (agent-first)

Keep one composition; Chat remains the hero surface.

| Change | Intent |
|--------|--------|
| Tool rows (from 11c) | See MCP work without leaving Chat |
| Sticky composer + clearer busy/queue | Cursor-like continuity |
| Collapsible “system/log” noise | Reduce clutter |
| WaitingInput kinds styled | `mcp_tool` / `plan_confirm` / shell distinct but same chrome |
| Markdown lite for assistant | Code fences readable; no emoji chrome |

### 15b. Streaming / errors / reconnect

| Issue | Fix |
|-------|-----|
| Delta storms re-render whole webview state | Throttle `postState` / incremental DOM update |
| App-server crash mid-turn | Surface `turn/error`; offer Reconnect; don’t leave ghost busy |
| WS drop | Status bar + Chat banner; auto-retry with backoff |
| Error taxonomy | User-safe messages: auth, network, MCP, gateway 429, internal |

### 15c. Product packaging

| Deliverable | Detail |
|-------------|--------|
| `apps/z-desktop/README.md` | One-path: clone → apply-product → build → run |
| CI job (optional) | Build extension + compile Python checks; artifact extension VSIX |
| Versioning | Align extension version with app-server protocol version notes |
| Escape matrix | Document `Z_USE_GATEWAY`, `Z_MCP_RUNTIME`, `Z_ALLOW_BYOK`, `Z_SKIP_ACCOUNT`, usage stub |

### 15d. End-to-end acceptance (definition of done for “completion”)

A reviewer can:

1. Launch **Z Editor** (branded) or extension-in-Seam with Z theme.
2. Sign in; open folder; Chat is center.
3. Connect GitHub (OAuth or PAT); Trust; ask for issues; see tool row + answer.
4. Open Profile; see **real** usage after a gateway turn (or honest empty).
5. Hit a Commit Gate override path still two-step.
6. Kill app-server; Reconnect recovers without reinstall.

---

## 11. IPC / protocol deltas (cumulative)

| Method / event | Phase | Notes |
|----------------|-------|-------|
| `mcp/test` (handshake) | 11 | Breaking soft-upgrade of result shape (`tools_count`) |
| `mcp/tools` (optional) | 11 | `{ connectionId } → tools[]` for panel debug |
| `mcp/tool_started\|finished\|error` | 11 | notifications |
| `mcp/oauthStart` | 13 | |
| `z-editor://mcp/oauth/done` | 13 | URI |
| `usage/summary` semantics | 14 | empty when unsigned-in |

Keep `ipc-v0.json` as source of truth; bump protocol notes / capabilities (`mcp_runtime`, `mcp_oauth`).

---

## 12. Security & product locks

| Rule | Enforcement |
|------|-------------|
| D9 first-use | No tools/call without confirm or prior trust |
| No secrets in IPC list | Continue stripping; runtime env only in process |
| Router-only models | Unchanged; MCP ≠ model BYOK |
| OAuth secrets server-side | Client id/secret stay on `z_server` |
| Tool output budget | Reuse `output_budget` before injecting MCP payloads into Chat/context |
| Uncertainty | Unverified MCP results may still raise detectors (keep) |

---

## 13. Test plan (by phase)

| Phase | Primary tests |
|-------|----------------|
| 11 | Fake stdio MCP server; first-use; turn fence/dispatch; secret stripping |
| 12 | `apply-product --check`; extension compiles; smoke script if CI has Electron |
| 13 | oauthStart URL shape (mock HTTP); URI handler refresh; PAT fallback still works |
| 14 | unsigned-in empty; signed-in mock gateway; no demo under `Z_GATEWAY_STUB` alone |
| 15 | Chat notification throttle unit; reconnect state machine; E2E checklist (manual) |

---

## 14. Cut lines (if schedule slips)

Cut **in this order** without killing the product pivot:

1. Phase 14d sparkline  
2. Phase 15a markdown polish  
3. Phase 12 Windows package  
4. Phase 13 Linear OAuth (keep GitHub OAuth)  
5. Phase 11 SSE/custom transport (keep GitHub stdio)  
6. **Never cut:** Phase 11 GitHub tools/call in Chat, first-use gate, usage honesty (14a), Z brand apply script (12a)

---

## 15. Implementation checklist (for executing agents)

```
[ ] Phase 11a SessionManager + fake-server tests
[ ] Phase 11b turn_runner / Coder wire (path A or B documented)
[ ] Phase 11c notifications + Chat tool rows + first-use path live
[ ] Phase 11d GitHub PAT E2E acceptance
[ ] Phase 12a apply-product script + brand assets
[ ] Phase 12b built-in extension / default layout
[ ] Phase 12c package script + README
[ ] Phase 13a catalog align OAuth+PAT
[ ] Phase 13b oauthStart + deep-link done
[ ] Phase 13c runtime credentials from /v1/mcp/runtime
[ ] Phase 14a–c usage honesty + metering audit
[ ] Phase 15a–d polish + packaging + E2E checklist
[ ] Update z-editor-v1-implementation-plan.md status → Phases 0–15
[ ] Update apps/z-desktop/README.md
[ ] Protocol + tests green; extension tsc clean
```

---

## 16. Suggested PR slicing (when implementing)

| PR | Scope | Base |
|----|-------|------|
| A | Phase 11 only | `main` or latest editor impl |
| B | Phase 12 only | `main` (parallel OK) |
| C | Phase 13 | after 11 (runtime) |
| D | Phase 14 | parallel OK |
| E | Phase 15 | after A + B minimum |

Do **not** mix branded Electron packaging and MCP runtime in one PR unless necessary — different failure modes.

---

## 17. Open questions (resolve at implement time, not blockers)

1. Function-calling (11b-A) vs fence protocol (11b-B) — choose by invasiveness in current Coder.
2. Whether OAuth tokens ever persist offline in `secrets.json` — default **no**.
3. Seam commit SHA to pin for Phase 12.
4. Whether GitHub OAuth app redirect allows custom scheme directly or only https callback → app deep link (https callback is the plan default).

---

## 18. Success statement

**Done** means: a developer opens **Z Editor**, signs in, connects **GitHub**, asks Chat to touch issues/PRs, sees a tool call with first-use trust, gets a real answer, and can open Profile to see real gateway usage — in a window that says **Z**, not Visual Studio Code.
