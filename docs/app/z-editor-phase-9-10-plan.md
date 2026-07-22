# Z Editor — Phase 9 & 10 Implementation Plan

**Date:** 2026-07-21 (implemented 2026-07-22)  
**Status:** Implemented on `cursor/z-editor-v1-impl-313a`  
**Branch:** `cursor/z-editor-v1-impl-313a`  
**Depends on:** Phases 0–8 (gateway usage API, app-server IPC, Profile stub, `mcp/list` stub)  
**Look:** Z Terminal palette (orange `#C96A2B` / black `#0A0A0A`)

---

## Goals

| Phase | Goal |
|-------|------|
| **9 — Profile / usage** | Show live spend & volume from `gateway_requests` in the Profile panel (billing period / all time). |
| **10 — MCP in-app** | Manage MCP connectors inside Z Editor (local-first), test before save, first-use confirm (D9), optional sync to `z_server`. |

---

## Phase 9 — Profile / usage

### 9a. Gateway aggregate (already partly exists)

**Cloud:** `GET /v1/gateway/usage?range=billing_period|all` → `{ by_model[], total_requests, total_cost_usd }` (live SQL, no rollup — D10).

**Gap:** app-server `usage/summary` is still a stub.

**Work:**
1. Implement `AppServerSession._usage_summary` to proxy authenticated `GET {Z_AUTH}/v1/gateway/usage`.
2. Return normalized shape for the extension:
   ```json
   {
     "range": "billing_period",
     "byModel": [{"model_id","requests","input_tokens","output_tokens","cost_usd"}],
     "total_requests": 0,
     "total_cost_usd": 0.0,
     "authenticated": true,
     "error": null
   }
   ```
3. When unsigned-in: empty series + `authenticated: false` (no hard fail — Profile still usable).

### 9b. Profile UI — table + bars + range

**Surface:** existing left **Profile** webview (keep auth/connection chrome).

**Add:**
- Range toggle: **Billing period** | **All time**
- Totals strip: requests · cost USD
- Horizontal bar chart per model (CSS width % of max requests or cost)
- Table: model · requests · in/out tokens · cost
- Refresh button (already present)

**IPC:** `usage/summary { range }`

**Tests:** unit-test handler with mocked `requests.get`; Profile still renders when unauthenticated.

---

## Phase 10 — MCP in-app

### Architecture (local-first + optional cloud)

```
┌─ Z Editor MCP panel ─────────────────────────────┐
│  catalog · local connections · cloud runtime     │
└──────────────┬───────────────────────────────────┘
               │ IPC
┌──────────────▼───────────────────────────────────┐
│  z-app-server                                    │
│   mcp/list|catalog|connect|disconnect|test       │
│   mcp/confirmFirstUse | firstUseStatus | sync    │
└──────┬─────────────────────────────┬─────────────┘
       │ ~/.z/mcp/*                  │ optional
       ▼                             ▼
  local JSON store              z_server /v1/mcp/*
```

### 10a. Local store + test connection

**Files:** `aider/z/mcp_local.py` (new)

| Store | Path | Contents |
|-------|------|----------|
| Connections | `~/.z/mcp/connections.json` | id, server_name, display_name, config (non-secret), secrets_ref / encrypted local blob, enabled, status, source=`local\|cloud` |
| First-use | `~/.z/mcp/first_use.json` | `{ server_id: { confirmed_at, tool_name? } }` |

**Secrets:** store under `~/.z/mcp/secrets/<id>.json` mode `0600` (local only; never in IPC list payloads).

**`mcp/test`:** validate required fields; for `command` check executable on PATH; for `server_url` attempt lightweight HTTP GET/HEAD with short timeout; return `{ ok, detail }` without persisting.

**`mcp/connect`:**
1. Optionally run test (fail closed unless `skipTest`)
2. Persist local
3. If signed in + `syncCloud: true` (default when authed): POST `/v1/mcp/connect`

**`mcp/list`:** merge local store + cloud `fetch_mcp_runtime()` (dedupe by server_name/id).

**`mcp/catalog`:** return curated list (reuse `z_server.services.mcp_catalog` when importable; else embed a thin local copy of custom/filesystem/github).

### 10b. First-use tool confirm (D9)

1. `mcp/firstUseStatus { serverId }` → `{ confirmed: bool }`
2. `mcp/confirmFirstUse { serverId, toolName? }` → persist approval
3. `AppServerIO.confirm_mcp_first_use(server_id, server_name, tool_name)`:
   - if confirmed → True
   - else `turn/waiting_input` kind=`mcp_tool` → wait for `turn/respond`
   - on yes → write first-use + return True; on no → False

Wire a public helper `aider.z.mcp_local.require_first_use(...)` for future tool-call sites; Chat already handles WaitingInput buttons.

### 10c. Optional sync to z_server

- `mcp/sync` — push local unsynced connections via `/v1/mcp/connect`; pull runtime into local cache markers
- Disconnect: delete local + if cloud id known POST disconnect

### 10 UI — MCP panel

New left sidebar view `z.mcp` (Z Terminal styling):
- List connections (local / cloud badges, enabled, status)
- Catalog dropdown → connect form (fields from catalog)
- Test · Connect · Disconnect
- First-use: show “needs first-use confirm” until approved; button **Trust this server**

---

## IPC additions

| Method | Phase |
|--------|-------|
| `usage/summary` (real proxy) | 9 |
| `mcp/catalog` | 10 |
| `mcp/list` (enriched) | 10 |
| `mcp/connect` | 10 |
| `mcp/disconnect` | 10 |
| `mcp/test` | 10 |
| `mcp/confirmFirstUse` | 10 |
| `mcp/firstUseStatus` | 10 |
| `mcp/sync` | 10 |

Notifications: reuse `turn/waiting_input` for MCP first-use; no new stream required for V1.

---

## Security

| Rule | How |
|------|-----|
| No secrets in list IPC | `public_dict` / strip secret keys |
| Local secrets 0600 | file perms on `~/.z/mcp/secrets` |
| First-use confirm | D9 — no silent auto-approve |
| Test before save | default; `skipTest` only for advanced |
| Provider keys | still gateway-only (unchanged) |

---

## Tests

| Case | File |
|------|------|
| usage/summary unauthenticated empty | `test_z_app_server_panels.py` |
| usage/summary proxies gateway JSON | mock requests |
| mcp local connect/list/disconnect | tmp Z_HOME |
| mcp/test fails without command/url | unit |
| first-use confirm gate | unit on store |
| override confirm still green | regression |

---

## Out of scope (later)

- Full MCP stdio session / tool invocation runtime inside app-server
- OAuth MCP connect from desktop (deep-link to web `/v1/mcp/oauth/start` is enough for V1)
- Usage cost estimation beyond gateway-logged `cost_usd`

---

## Ship checklist

1. Plan doc (this file) — done  
2. Phase 9 backend + Profile UI — done (`usage_client.py`, Profile charts)  
3. Phase 10 `mcp_local` + handlers + MCP webview — done  
4. Protocol + README + north-star status — done  
5. Tests + compile + push — done  

## Implementation map

| Piece | Path |
|-------|------|
| Usage client | `aider/z/usage_client.py` |
| MCP local store | `aider/z/mcp_local.py` |
| IPC handlers | `aider/z/app_server/handlers.py` |
| First-use IO | `aider/z/app_server/io_bridge.py` (`confirm_mcp_first_use`) |
| Profile UI | `apps/z-desktop/extension/src/views.ts` |
| MCP UI | `apps/z-desktop/extension/src/mcpView.ts` |
| Protocol | `apps/z-desktop/protocol/ipc-v0.json` |
| Tests | `tests/basic/test_z_app_server_panels.py` |
