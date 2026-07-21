# Z Editor V1 — Implementation Plan (post–Codex audit)

**Status:** Phase 0–5 landing — agent-first layout + turn loop + **gateway TaskMode/Intent routing** (`v1-taskmode`); Phases 6–10 next.  
**Date:** 2026-07-21 (revised: Phase 5 real routing policy)  
**Inputs:** user revised V1 scope; local clones `vendor/codex` + `vendor/vscode`; Z monorepo surfaces.  
**Related:** [`z-desktop-north-star.md`](./z-desktop-north-star.md), router-only CLI (#147).

---

## TL;DR

| Decision | Plan lock |
|----------|-----------|
| Goal | Desktop editor; Z engine programs; **router-only** models; uncertainty / skills / gate / cost visible in-app |
| **Editor shell** | **[microsoft/vscode](https://github.com/microsoft/vscode)** via fork **[Nate-git05/Seam](https://github.com/Nate-git05/Seam)** — Electron workbench, Monaco, file tree, tabs. Local: `apps/z-desktop/vendor/vscode` |
| Codex role | **Protocol + MCP/session patterns only** — CLI/TUI/`app-server`; **not** a desktop GUI. Fork **[Nate-git05/codex](https://github.com/Nate-git05/codex)**. Local: `apps/z-desktop/vendor/codex` |
| Agent brain | Existing Z (Python/Aider fork) |
| Stack change | **Drop Tauri greenfield** for V1 shell — fork/brand VS Code OSS instead (Cursor-shaped). React panels can still mount inside VS Code webview/workbench contributions. |

Said plainly: **VS Code = the frame. Codex = the agent IPC playbook. Z = the brain.**

---

## 0. Scope locks (confirm before building)

These match the user’s Section 0, with one Codex-reality amendment.

### 0.1 Editor shell = VS Code OSS; Codex = protocol patterns — **locked**

| Prior assumption | After audit + VS Code clone |
|------------------|-----------------------------|
| Fork Codex as desktop app shell | **False.** Codex has no desktop GUI. |
| Fork / brand VS Code OSS | **True — V1 editor foundation.** Electron + workbench + Monaco. |
| Codex useful surface | `app-server-protocol`, client/transport, MCP/config/session lifecycle as *reference* |
| Discard from Codex | `codex-rs/core` agent brain, ChatGPT auth, Codex model stack |

**Licenses:**
- VS Code OSS: **MIT** (`LICENSE.txt`) — commercial fork OK; keep notices / `ThirdPartyNotices.txt`.
- Codex: **Apache-2.0** — carry `NOTICE` if copying code.

**Product branding:** change `product.json`, app name, icons, update URLs — ship as **Z Editor**, not “Code - OSS” / VS Code.

### 0.2 “Sign in through Z”

**Lock:** Google OAuth **+** Z-native account (email + password *or* magic link — product pick: prefer **magic link** to avoid password-store complexity; password acceptable if already in `z_server`).

**Already exists in Z today:**
- Google OAuth: `z_server/services/google_oauth.py`, dashboard routes `/app/login/google/*`
- Email / phone providers: `z_server/models/user.py` `AuthProvider` enum (`email` | `phone` | `google`)
- CLI web login: `aider/z/auth.py`, `aider/z/login_screen.py`

**Not “SSO through a third party”** beyond Google. Managed auth (Clerk/Auth0/Supabase) remains optional — see D7 below: **prefer extending `z_server` for V1** unless timeline forces buy.

### 0.3 Uncertainty “linked list”

**Lock:** vertical **chain-of-cards** (CSS connector line), primary sort = risk descending, secondary sorts = age / type / status. **Not** a free-form graph editor (no React Flow in V1).

Maps cleanly to existing `UncertaintyNode.risk_tier` + `TIER_RANK` in `aider/z/uncertainty/schema.py`.

---

## 1. Codex codebase audit (what we actually cloned)

**Path:** `apps/z-desktop/vendor/codex` (gitignored local vendor; ~82MB shallow).

### 1.1 Layout

| Path | Role | V1 reuse |
|------|------|----------|
| `codex-rs/` | Rust workspace (~100+ crates) — center of gravity | Protocol + sandbox/MCP reference |
| `codex-rs/tui/` | Ratatui terminal UI | UX patterns only (not widgets) |
| `codex-rs/app-server*` | Rich-client JSON-RPC server/client/protocol | **Primary reuse target** |
| `codex-rs/core/` | Agent brain / thread manager | **Replace with Z** |
| `codex-rs/login/`, `chatgpt/`, `model-provider*` | OpenAI auth & models | Replace with Z auth + gateway |
| `codex-rs/codex-mcp/`, `config/` | MCP servers + TOML config | Patterns for connector mgmt |
| `codex-rs/exec*`, `sandboxing/`, `execpolicy/` | Local command execution / approvals | Optional later (Z already shells via Aider) |
| `codex-rs/skills/`, `core-skills/` | File-based `SKILL.md` packs | Different from Z skills — do not merge schemas |
| `codex-cli/`, `sdk/` | npm launcher + TS/Python SDK wrapping CLI JSONL | Inspiration for host embedding |
| `cli/.../desktop_app/` | Opens/installs external Codex.app DMG/Store | Proof desktop UI is **out of repo** |
| `docs/` | Thin pointers to external docs | — |

### 1.2 How Codex clients talk to the agent

From `codex-rs/app-server/README.md`:

1. Transport: **stdio JSONL** (default), experimental WS, unix-socket WS  
2. Handshake: `initialize` → `initialized`  
3. Lifecycle: `thread/start|resume` → `turn/start` → stream notifications → `turn/completed`  
4. Primitives: **Thread / Turn / Item**  
5. Schema export: `codex app-server generate-ts` / `generate-json-schema`

**Implication for Z Editor:** do **not** pretend we can “drop Z into Codex core.” Instead:

- Define **`z-app-server`** (Python) implementing a *similar* JSON-RPC lifecycle over **local WebSocket** (user stack).
- Optionally keep Thread/Turn/Item naming for familiarity, but payload types are Z-native (uncertainty nodes, skill events, gate blocks, router usage).

### 1.3 What Codex is *not*

- Not Tauri  
- Not Electron app source  
- Not Monaco  
- Not a visual uncertainty tree  
- Not Z’s verify gate  

---

## 2. Z monorepo audit (brain — what already exists)

### 2.1 Uncertainty — **strong, reusable**

| Piece | Location | Notes |
|-------|----------|-------|
| Node schema | `aider/z/uncertainty/schema.py` | `risk_tier`, `confidence_tier`, status, files/symbols |
| Resolution contracts | `aider/z/uncertainty/resolution.py` | Attached at create (`store.add`) |
| Local store | `aider/z/uncertainty/store.py` | `~/.z/uncertainty/*.json` |
| Remote sync API | `aider/z/uncertainty/remote.py`, `z_server` uncertainty models | Nodes can sync |
| CLI surface | `/uncertainties`, `ui.print_summary_line` | Terminal today |

**Gap for V1 UI:** chain visualization + live IPC events (`node/created`, `node/updated`, `node/resolved`). Store already supports list/filter — UI is new.

### 2.2 Skills — **strong, reusable**

| Piece | Location | Notes |
|-------|----------|-------|
| Schema | `aider/z/skills/schema.py` | `source` ∈ paste\|generate\|capture; `quality_state`; `needs_review` |
| Vector index | `aider/z/skills/vector.py` | Chroma-backed |
| Author / generate CLI | `aider/z/skills/cli.py`, `generate.py` | Manual create already forces `draft` / `needs_review` |
| Retrieve / router | `aider/z/skills/session.py`, `router.py` | |

**Gap:** in-app viewer + author form; IPC CRUD; extend `source` with explicit `manual` (or reuse `paste`/`generate` — recommend add `manual` without breaking).

**D8 already mostly true in CLI:** hand-authored paths set `quality_state=draft`, `needs_review=True`. Keep that for app authoring.

### 2.3 Commit block / verify gate — **engine yes, ledger no**

| Piece | Location | Notes |
|-------|----------|-------|
| Gate | `aider/z/uncertainty/gate.py` | Blocks commit; `emit_commit_blocked` |
| Verify | `aider/z/uncertainty/verify.py` | `TESTS_FAILED`, etc. |
| Escape | `Z_SKIP_VERIFY_GATE` | Exists — must appear in commit-block UI when used |

**Gap:** **no durable cross-thread commit-block history**. Today blocks are ephemeral scrollback / session messages. V1 needs a local (SQLite or JSONL under `~/.z/commit_blocks/`) append-only ledger keyed by repo + thread/session id, written wherever `emit_commit_blocked` fires.

### 2.4 Model routing — **local policy yes, gateway no**

| Piece | Location | Notes |
|-------|----------|-------|
| Registry / select / escalate | `aider/z/routing/*` | Tiered cheapest-good-enough |
| Router-only onboarding | `aider/z/cli.py`, `onboarding.py` | V1 CLI path (#147); BYOK behind `Z_ALLOW_BYOK` |

**Gap:** **cloud routing gateway** that holds provider keys, streams completions, logs `requests` for profile/usage. Local `select_model` becomes the policy engine *called by* the gateway (or mirrored), not something that holds Anthropic/OpenAI keys on the desktop.

### 2.5 Auth — **mostly exists**

| Piece | Location |
|-------|----------|
| Google + email/phone | `z_server` User / AuthProvider |
| CLI session tokens | `aider/z/credentials.py`, web login |

**Gap for desktop:** embedded auth UI (system browser + deep link / localhost callback), token storage in OS keychain (Codex `keyring-store` pattern), no ChatGPT login.

### 2.6 MCP — **cloud catalog yes, local runtime thin**

| Piece | Location | Notes |
|-------|----------|-------|
| DB model | `z_server/models/mcp.py` | Encrypted credentials |
| API + dashboard | `z_server/routers/mcp.py`, dashboard | Connect/list/disconnect |
| CLI fetch | `aider/z/mcp_client.py` | Pulls `/v1/mcp/runtime` for session |

**Gap vs user V1:** in-app connector management + **local** connection config (user doc says local-first for V1). Recommendation: dual-write — local SQLite for machine-local servers; optional sync to workspace MCP on `z_server` when signed in. First-use tool confirm (D9) needs a Z tool-approval hook that does not exist as a first-class UI today.

### 2.7 Turn orchestrator — **ready for IPC**

`aider/z/turn_ux.py` `TurnOrchestrator`: Idle / Busy / WaitingInput, queue, `on_state_change`, `on_queue_change`.

**Gap:** serialize these as WS notifications; map Busy/WaitingInput to chat panel chrome (one busy line — aligns with quiet-turn work).

### 2.8 Usage metering — **missing**

No `requests` / spend table found under `z_server/models`. Profile view **requires** new gateway schema (Section 4).

### 2.9 `web/` Next.js

Marketing + login + MCP dashboard. **Not** the desktop editor. Reuse auth pages/patterns; do not ship Monaco inside Next for V1 desktop.

---

## 3. Target architecture (revised with audit)

```
┌──────────────────────── Z Editor (Tauri) ──────────────────────────┐
│  React + TS                                                         │
│  Monaco │ Chat │ Uncertainty chain │ Skills │ Commit-blocks │       │
│  Profile/usage │ MCP connectors                                     │
│                         │                                           │
│              local WebSocket JSON-RPC (z-app-server)                │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
              ┌───────────▼────────────┐
              │  z-app-server (Python) │  ← NEW thin host process
              │  wraps Coder + stores  │
              │  TurnOrchestrator IPC  │
              └───────────┬────────────┘
                          │ HTTPS (user JWT)
              ┌───────────▼────────────┐
              │  Routing gateway       │  ← NEW cloud service
              │  auth, keys, route,    │
              │  stream, requests log  │
              └───────────┬────────────┘
                          │
                   providers (no keys on client)
```

**Codex influence (dashed conceptually):** app-server lifecycle (`initialize` / thread / turn / item events) informs `z-app-server` method names and streaming shape — not a binary dependency on `codex-core`.

---

## 4. Stack (confirmed + notes)

| Layer | Choice | Audit note |
|-------|--------|------------|
| Desktop shell | **VS Code OSS (Electron)** | Fork/brand `vendor/vscode` — not Tauri |
| UI | VS Code workbench + webview panels (React/TS where needed) | Z sidebars/panels as contributions |
| Editor | Monaco (already inside VS Code) | Do not re-embed a second Monaco |
| Uncertainty chain | Plain React + CSS connectors | No graph lib |
| Agent | Z Python | Existing |
| Local IPC | JSON-RPC over WS | Align with Codex app-server *semantics*; prefer WS as user specified (Codex marks WS experimental — our server, our rules) |
| Gateway | New service (FastAPI sibling of `z_server` or module inside it) | Prefer **extend `z_server`** first to avoid two auth stacks |
| Auth | Google + Z-native | Prefer `z_server` over Clerk unless schedule slips |
| Skills store | Existing Chroma/SQLite under `~/.z` | Add `source=manual` |
| Usage | Postgres `requests` on gateway | New |

---

## 5. Component breakdown → build units

### A. Gateway (cloud) — highest stakes

**Jobs:** authenticate user; never expose provider keys; accept chat/completions-like stream requests from local Z; run routing policy (reuse `aider/z/routing` logic server-side or call shared package); log every request for profile.

**MVP rule:** one hardcoded route (e.g. preferred model → fallback tier) before full TaskMode-aware policy.

**Tables (new):**
- `users` — already exists; ensure `primary_provider` covers google/z_native  
- `requests` — id, user_id, model_id, tier, input/output tokens, cost_usd, latency_ms, status, created_at, thread_id, task_mode  
- `routing_policy_version` — string/semver for experiments  

### B. z-app-server (local Python)

**Jobs:** spawn/manage agent session for a workspace cwd; expose JSON-RPC methods; push notifications.

**Suggested methods (v0):**

| Method / event | Purpose |
|----------------|---------|
| `initialize` / `initialized` | Client handshake (Codex-shaped) |
| `workspace/open` | Set root, load stores |
| `turn/start` | User prompt → Z `run_one` |
| `turn/*` notifications | Busy phase, model stream, WaitingInput |
| `uncertainty/list` / `uncertainty/subscribe` | Chain view |
| `skills/list|get|create|update` | Viewer + author |
| `commit_blocks/list` / `override` | Cross-thread ledger |
| `mcp/list|connect|disconnect|test` | Connectors |
| `usage/summary` | Proxy to gateway `requests` aggregate |
| `auth/status` | Session state |

### C. VS Code OSS shell (branded Z Editor)

Fork/brand Code - OSS. Inherit editor chrome (Monaco, file tree, tabs). Own process lifecycle for `z-app-server`. Add six Z panel groups as workbench contributions / webviews.

### D. Uncertainty chain view

- Data: `UncertaintyNode` + embedded `resolution_contract` from `signals`  
- Sort: risk → age → type  
- Live: subscribe to store mutations over IPC  

### E. Skills viewer + author

- Filters: kind, quality_state, needs_review, text search on triggers/capability  
- Author form → `skills/create` with `source=manual`, `quality_state=draft`, `needs_review=true`  
- Near-dup merge should eventually run on manual creates (reuse `near_dup.py`) — schedule with skills UI, not as silent trust bypass  

### F. Commit-block view

- New ledger writer beside `emit_commit_blocked`  
- Fields: id, repo_key, session_id, thread_id?, reason, verify_state, created_at, state ∈ blocked\|overridden\|resolved, override_meta  
- UI: list + confirm override (never one-click); deep-link to originating chat if id known  

### G. Profile / usage

- Read-only aggregates from `requests` (D10: live query, no rollup table)  
- Time range: billing period / all time  

### H. MCP connector management

- Local config store + optional sync to `z_server` MCP API  
- Test connection before save  
- D9: first tool invocation from a newly connected server → confirm once  

---

## 6. Decisions matrix (D1–D10 + Codex-specific)

| # | Question | Lock | Notes |
|---|----------|------|-------|
| D1 | Routing policy location | Gateway | Local Z must not hold provider keys |
| D2 | Local IPC | WebSocket JSON-RPC | Codex-inspired methods |
| D3 | Provider keys on desktop | Never | Non-negotiable |
| D4 | Gateway down | Clear disconnected; local features work | Uncertainty/skills/MCP local still usable offline; turns that need models fail loudly |
| D5 | Uncertainty viz | Chain-of-cards in V1 | Not graph |
| D6 | MCP management | In-app V1 | Local-first + optional cloud sync |
| D7 | Auth | **Extend `z_server` first** (Google + Z-native) | Buy Clerk/etc. only if calendar slips |
| D8 | Manual skill trust | draft / needs_review | Matches current CLI generate path |
| D9 | New MCP tool trust | Confirm first use per server | Align with command-risk thinking |
| D10 | Usage freshness | Live from `requests` | No rollup in V1 |
| **D11** | Codex reuse mode | Protocol inspiration — not desktop fork | Audit finding |
| **D12** | Gateway vs `z_server` | Prefer gateway module inside `z_server` for V1 | One auth domain |
| **D13 (new)** | Editor foundation | **VS Code OSS fork** (not Tauri greenfield) | User provided `microsoft/vscode` clone |

---

## 7. V1 scope lock (unchanged intent, clearer cut line)

### In V1

1. **VS Code OSS fork** branded as Z Editor (workbench + Monaco + file tree) + chat panel  
2. z-app-server + TurnOrchestrator-driven UI state  
3. Routing gateway (no BYOK) + preferred model + tier escalate  
4. Uncertainty chain (risk sort + secondary sorts)  
5. Skills viewer + manual author (draft trust)  
6. Commit-block cross-thread ledger + UI  
7. Profile / usage from `requests`  
8. In-app MCP connector management + first-use confirm  
9. Google + Z-native sign-in  

### Out of V1

- Free-form uncertainty graph  
- Workspace groups  
- Full marketplace / arbitrary VS Code extension compatibility (may come free-ish with OSS; not a V1 goal)  
- Learned/ML routing  
- Spend alerts / budget caps  
- Vendoring full vscode/codex trees into git  
- Tauri greenfield shell  
- Passwordless passkeys (unless already trivial)  

### Pressure cuts (bottom of build order first)

If timeline slips, cut in this order **without** killing the pivot:

1. MCP management UI (keep cloud dashboard)  
2. Profile/usage polish (keep `requests` logging)  
3. Commit-block cross-thread view (keep per-turn gate)  
4. Skills authoring (keep viewer + CLI author)  
5. **Never cut:** gateway + router-only + IPC turn loop + editor/chat  

---

## 8. Build order (revised with dependencies)

```
Phase 0 — Locks & scaffolding
  0a. Confirm §0 + D7/D11/D12/D13 (VS Code fork locked)
  0b. Brand vendor/vscode → Z Editor (product.json, name, icons) — build once
  0c. apps/z-desktop/z-app-server skeleton + IPC schema v0
  0d. Empty Z workbench contribution (sidebar container)

Phase 1 — Gateway skeleton (parallel with 0b–0d)
  1a. Auth (Google + Z-native) reuse from z_server
  1b. Provider key vault (server-only)
  1c. Stream proxy + hardcoded route
  1d. requests logging

Phase 2 — Wire Z model calls through gateway
  2a. Replace direct litellm/provider calls when auth_mode=router
  2b. End-to-end: CLI or thin client → gateway → model
  2c. Kill BYOK as product path (already started #147)

Phase 3 — VS Code shell lifecycle
  3a. Open folder / tabs / save (inherited from Code - OSS)
  3b. Spawn/attach z-app-server from Z contribution
  3c. Auth deep-link / sign-in UX in workbench

Phase 4 — IPC + turn orchestrator → UI
  4a. Chat panel: prompt in, stream out, Busy/WaitingInput
  4b. Approvals / plan confirm as WaitingInput surfaces

Phase 5 — Real routing policy
  5a. Port TaskMode/Intent → tier selection on gateway
  5b. Escalation + calibration hooks

Phase 6 — Uncertainty chain view
  6a. list + sort + expand (ResolutionContract)
  6b. live subscribe

Phase 7 — Skills viewer + author
  7a. list/detail filters
  7b. create form → draft trust + near-dup check

Phase 8 — Commit-block ledger + view
  8a. persist on emit_commit_blocked
  8b. list + override UX

Phase 9 — Profile / usage
  9a. gateway aggregate API
  9b. simple table/bars + time range

Phase 10 — MCP in-app
 10a. local store + test connection
 10b. first-use tool confirm
 10c. optional sync to z_server
```

Phases 6–10 can parallelize after Phase 4 if staffing allows; Phase 10 is most self-contained.

---

## 9. IPC event sketch (Codex-shaped, Z payloads)

```text
→ initialize { clientInfo, workspaceRoot? }
← initialize.result { serverInfo, zHome, capabilities }

→ turn/start { threadId, text }
← turn/started
← turn/busy { phase: "Working…" | "Waiting for model…" }
← item/agentMessage/delta { text }
← uncertainty/upsert { node }
← gate/commit_blocked { record }     # also appends ledger
← turn/waiting_input { kind: plan_confirm|shell|mcp_tool|… }
→ turn/respond { threadId, payload }
← turn/completed { usage? }

→ uncertainty/list { sort: risk|age|type }
← uncertainty/list.result { nodes[] }

→ skills/create { skill }
← skills/create.result { skill }     # draft / needs_review

→ commit_blocks/list {}
→ mcp/connect { name, command|url, … }
```

Exact schema lands in `apps/z-desktop/protocol/` as JSON Schema + generated TS types (mirrors Codex’s `generate-ts` idea without depending on their binary).

---

## 10. Security (expanded)

| Surface | Rule |
|---------|------|
| Provider keys | Gateway only |
| User JWT | Short-lived access + refresh; desktop keychain |
| MCP | Secrets encrypted at rest; first-use confirm; no silent auto-approve |
| Commit override | Explicit confirm + audit row |
| Gateway | Authn + rate limit; usage log as abuse signal |
| Codex code | If copying Rust crates later, preserve Apache notices |

---

## 11. Risks

| Risk | Mitigation |
|------|------------|
| **“Fork Codex desktop” misconception** | Use VS Code for GUI; Codex for protocol only |
| Five UI subsystems in one V1 | Ordered cuts in §7 |
| Manual skills + loose triggers | D8 + near-dup on create |
| MCP attack surface | D9 + security pass before broad ship |
| Gateway unit economics unknown | Log `requests` from day one; reuse P2 harness for routing quality |
| Latency: local Z + gateway + provider | Stream early; Busy line honesty |
| Auth buy-vs-build churn | Default extend `z_server` |
| No usage table today | Phase 1 must add `requests` before Profile (Phase 9) |

---

## 12. Mapping: user doc sections → this plan

| User section | Disposition |
|--------------|-------------|
| §0.1 Codex foundation | Amended — protocol/shell patterns, not GUI fork |
| §0.2 Z sign-in | Locked Google + Z-native |
| §0.3 Linked list | Locked chain-of-cards |
| §1 Architecture | Kept; gateway + local Z; GUI is VS Code OSS fork |
| §2 Stack | Kept; uncertainty viz = CSS chain |
| §3 Components | Kept; backed by EXISTING/MISSING audit in §2 |
| §4 Data model | Kept; `requests` is **new**; skills `source` extend |
| §5 Decisions | Kept + D11/D12 |
| §6 Security | Kept + MCP |
| §7–8 Scope/order | Kept with pressure cuts |
| §9 Risks | Kept + Codex misconception |

---

## 13. Immediate next actions (after plan approval)

1. Owner confirms §0 + D7/D11/D12.  
2. Open tracking issues / epic for Phases 1–4 only (do not ticket all five UI subsystems until gateway+IPC green).  
3. Brand + build `vendor/vscode` as Z Editor; add `z-app-server` — **do not** commit vendor trees.  
4. Keep `vendor/codex` as read-only protocol reference.  
5. Continue router-only CLI (#147) as the model-path precondition for Phase 2.

**Do not start** uncertainty/skills panels until Phase 1–2 prove gateway streaming and Phase 0b builds Code - OSS.

---

## Appendix A — Key Codex files to keep reading

- `apps/z-desktop/vendor/codex/codex-rs/app-server/README.md`  
- `.../app-server-protocol/src/protocol/v2/{thread,turn,item}.rs`  
- `.../app-server-client/src/lib.rs`  
- `.../tui/src/app_server_session.rs`  
- `.../codex-mcp/src/connection_manager.rs`  
- `.../cli/src/desktop_app/mod.rs` (external app handoff)  
- `.../LICENSE`, `NOTICE`

## Appendix B — Key Z files to hang UI on

- `aider/z/uncertainty/{schema,store,resolution,gate,ui}.py`  
- `aider/z/skills/{schema,cli,store,vector,near_dup}.py`  
- `aider/z/turn_ux.py`  
- `aider/z/routing/{select,escalate,registry}.py`  
- `aider/z/mcp_client.py`  
- `z_server/models/{user,mcp,uncertainty,skill}.py`  
- `z_server/routers/{mcp,dashboard}.py`
