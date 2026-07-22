# Z — UI/UX Redesign Plan (Codex-inspired, progressive disclosure)

**Date:** 2026-07-22  
**Branch:** `cursor/z-ui-redesign-impl-313a`  
**Status:** Implemented on `cursor/z-ui-redesign-impl-313a` (Phases 0–3)  
**Product:** Z Editor (agent-first Chat)  
**Inputs:** User redesign spec (“soft black / sherbet”), Profile reference (token heatmap + hover model/tokens), current stacked-sidebar screenshot, prior soft-UI work (`z-soft-ui-design.md` / PR #159)

---

## 0. Problem statement (agreed)

Today the left activity area stacks **five always-open webviews** (Uncertainty, Skills, MCP, Profile, Commit Gate). Empty states still consume vertical space. Chat — the product — feels like leftover width.

Codex’s pattern: **conversation is the only thing at rest**. Everything else is collapsed, contextual, or one click away.

**Core principle:** progressive disclosure over permanent sidebar real estate.  
If a panel has nothing to say, it is not visible.

This plan also absorbs the Profile ask: **Profile should feel like the Codex profile** (avatar, stats strip, token-activity heatmap, insights) but in Z’s **soft black / sherbet** palette — with **model names and token usage visible on hover**.

---

## 1. Success criteria

1. Default viewport = **Chat only** (brand + activity strip + transcript + composer). No five-panel stack.
2. Secondary surfaces open **on demand** (flyout / drawer / overlay) or **auto when relevant** (first uncertainty, commit blocked).
3. Empty states never occupy a full open panel (unlit rail icon / badge only).
4. Commit Gate is a **composer status pill** when clear; expands to a `ListRow` drawer when blocked or clicked.
5. Profile is an **avatar popover / Profile page**, not a permanently open sidebar slab — includes token heatmap + model hover tooltips.
6. Palette is **warm soft-black + one sherbet orange**; no pure `#000`/`#FFF`; no purple/blue secondary accents.
7. UI chrome uses **sans**; monospace only for code/tool rows.
8. Layout changes do not reflow Chat width mid-turn (overlays/drawers, not push-sidebars).

---

## 2. Constraints (VS Code / Seam reality)

Z Editor today is a **VS Code extension** (`apps/z-desktop/extension`) hosting Chat as a center webview and secondary surfaces as `WebviewView`s in the activity bar / secondary sidebar.

Native VS Code **cannot** perfectly clone Codex’s custom 56px rail + flyouts without choosing a shell strategy:

| Path | What we get | Cost |
|------|-------------|------|
| **A. Extension-native (recommended V1)** | Activity Bar icons remain the “rail”; each click opens a **panel overlay webview** or **QuickPick / WebviewPanel** over Chat; Commit Gate pill lives **inside Chat HTML**; Profile becomes Chat-footer avatar → overlay | Fits current install (VSIX). Honest progressive disclosure without forking workbench. |
| **B. Chat-owns-chrome** | Single full-bleed Chat webview implements rail + flyouts + Profile page entirely in HTML/CSS | Best visual fidelity in VSIX; activity-bar views deprecated or thin adapters. |
| **C. Branded Electron (Seam)** | Full custom workbench chrome later | Path B packaging; heavier; after VSIX UX lands. |

**Plan decision for implementation order:**  
**Phase 1–3 = Path A + Chat-owned Commit Gate / Profile overlays (Path B for those two).**  
**Phase 4 = optional full Chat-owned rail.**  
**Phase 5 = Seam polish if needed.**

Do **not** start with a full workbench fork.

---

## 3. Color palette — “soft black / sherbet” (canonical)

Replace prior soft-UI hexes with this locked set (warm undertone, one saturated accent):

| Token | Hex | Use |
|-------|-----|-----|
| `bg-base` | `#0E0D0C` | App / Chat background |
| `bg-surface` | `#161412` | Panels, rail flyouts |
| `bg-surface-raised` | `#1E1B18` | Composer shell, modals, hover |
| `border-subtle` | `#2A2622` | Hairlines |
| `text-primary` | `#F2EDE7` | Body |
| `text-secondary` | `#9C948A` | Meta |
| `text-muted` | `#655F58` | Placeholder / disabled |
| `accent-sherbet` | `#F7A56B` | Live / selected / Send / attention |
| `accent-sherbet-dim` | `#C98858` | Hover/pressed |
| `accent-sherbet-wash` | `#F7A56B14` | Selected row / pending wash |
| `status-blocked` | `#D97757` | Blocked / −N (in-family) |
| `status-ok` | `#8FAE8B` | Ready / +N (desaturated sage) |

**Rules (non-negotiable):**

- Never pure black/white.
- Orange is a **signal**, not chrome fill (no orange section headers at rest).
- One accent. Status colors stay desaturated so sherbet remains the only saturated hue.
- Activity-strip deltas: `+N` → `status-ok` **or** soft sherbet-bright; `−N` → `status-blocked` — **still no traffic-light green/red**. Prefer: `+N` = `accent-sherbet`, `−N` = `accent-sherbet-dim` if we must keep “both orange”; **decision locked in Phase 0:**  
  **`+N` = `#F7A56B`, `−N` = `#C98858`** (both sherbet family). Sage reserved for Commit Gate “Ready” dot only.

Map into:

- `aider/z/theme.py`
- `apps/z-desktop/extension/src/zTheme.ts`
- `themes/z-terminal-color-theme.json`
- Update / supersede `docs/app/z-soft-ui-design.md` tokens

---

## 4. Layout architecture

```
┌────────┬──────────────────────────────────────────┬────────┐
│  Rail  │              Chat (center)                │ Context │
│ ~48–56 │         always full-height, primary        │ drawer  │
│ icons  │                                            │ closed  │
│        │                                            │ default │
└────────┴──────────────────────────────────────────┴────────┘
```

### 4.1 Left rail (collapsed by default)

**V1 (Path A):** VS Code Activity Bar = rail. Icons only:

| Icon | Opens |
|------|--------|
| Chat | Center Chat (already) |
| Uncertainty | Flyout / badge-driven |
| Skills | Command-palette overlay |
| MCP | Flyout connection list |
| Profile | Avatar menu / Profile overlay |
| (Sessions — Phase 4) | History list |

Hover = native tooltip. Click = **overlay**, not permanent stacked views.  
**Remove** the always-visible multi-`WebviewView` stack as the default experience (`package.json` views still registered but **hidden until summoned**, or replaced by overlay commands).

### 4.2 Center Chat (default = entire app)

Always visible:

1. Brand **Z** (hero-level, not an eyebrow)
2. Activity strip (two-line; soft tokens)
3. Transcript
4. Sticky composer (rounded, `bg-surface-raised`)
5. Composer footer: Commit Gate **pill** + queue hint

Welcome copy stays: “you prompt, Z programs” — layout must commit to that.

### 4.3 Right context drawer (closed by default)

Auto-open only when:

| Trigger | Opens |
|---------|--------|
| First `uncertainty/upsert` this turn with open nodes | Uncertainty drawer |
| `gate/commit_blocked` | Commit Gate drawer |
| User pins / clicks rail | That panel |

Auto-collapse:

- Uncertainty → badge count when all resolved
- Commit Gate → composer pill when clear

**Motion:** 150–200ms slide+fade overlay; **never** push-resize Chat mid-stream.

---

## 5. Component specs

### 5.1 Shared `ListRow` (reusable)

```
[status glyph]  Title, truncated…                         [time]
                author/meta · secondary               [+N −N]
```

- Transparent default; hover `bg-surface`; selected = `accent-sherbet-wash` + 1px sherbet left edge
- Status dot on glyph: none / `status-ok` / `status-blocked`
- Title single-line ellipsis; meta `text-secondary`; time `text-muted`
- Diff stats tabular-nums; `+N` sherbet, `−N` sherbet-dim (see §3)

**Used by:** Commit Gate drawer, future PR list, Skills overlay results (replace cards).

### 5.2 Uncertainty

| State | UI |
|-------|-----|
| Empty | Rail icon unlit — **no panel** |
| Open nodes | Badge count on rail; drawer auto-opens on first node |
| Resolved | Badge clears; drawer collapses |

Live indicator = soft sherbet **pulse dot**, not a loud “LIVE” chip.

### 5.3 Skills

- Not a persistent filter farm.
- Overlay: searchable list (`ListRow`), filters inside overlay, ⌘K / rail icon.
- First-run empty education inside overlay only.

### 5.4 MCP

- Rail icon → flyout connection list.
- Onboarding copy **once** (globalState flag); afterward list-only.

### 5.5 Commit Gate

| State | UI |
|-------|-----|
| Clear | Composer pill: sage dot + “Ready” |
| Blocked | Orange/blocked dot + “N blocked”; auto-open drawer |
| Expanded | Tabs `All / Blocked / Cleared` + search + `ListRow`s |

### 5.6 Queued messages

Keep behavior; restyle: `bg-surface-raised` + left border `accent-sherbet-wash`.

### 5.7 Profile (Codex-like — explicit)

**Entry:** bottom of rail / Chat chrome avatar → popover with “Open Profile”.

**Profile overlay / page sections:**

1. **Header** — avatar (sherbet wash + initials), display name, handle/email, plan badge (`Free` / workspace), actions: Share (defer), Private (defer), Edit (sign-in/out, reconnect).
2. **Stats strip** (pill, five cells, hairline dividers):  
   Total tokens · Peak tokens · Longest chat · Current streak · Longest streak  
   *(streaks may be stubbed until backend exists — show “—” honestly)*
3. **Token activity** — year heatmap (GitHub-style grid):  
   - Cells: `bg-surface` → sherbet wash → sherbet by intensity  
   - Toggles: Daily / Weekly / Cumulative  
   - **Hover tooltip (required):**  
     `date`  
     `model: z-composer` (or each model that day)  
     `in/out tokens · requests · $cost`  
     If multiple models that day: stacked lines per model.
4. **Insights** — Fast mode / most-used model / skills used / chats (wire what we have; stub remainder with muted “—”).
5. **Most used tools / MCP** — e.g. `@github · N runs` when available.

**Data:**

| Field | Source today | Gap |
|-------|--------------|-----|
| byModel tokens/cost | `usage/summary` | Keep |
| Daily heatmap series | — | **New** `usage/activity` (or extend summary) |
| Streaks / longest chat | — | Phase 2 stub → Phase 3 API |
| Model names on hover | byModel ids | Shorten provider prefix |

**Hover behavior:** native `title` insufficient — use a small floating tooltip in the Profile webview (`position: fixed`, `bg-surface-raised`, `border-subtle`, 12px sans).

---

## 6. Typography & density

| Layer | Font |
|-------|------|
| UI chrome (labels, tabs, buttons, Profile) | Humanist sans — prefer **IBM Plex Sans** or **Source Sans 3** (avoid Inter-as-default cliché if possible; still OK if already loaded). **Not** mono for chrome. |
| Code / tool rows / activity strip counts | IBM Plex Mono / JetBrains Mono (current) |

Density: more padding, section gaps; lists use `ListRow` not stacked cards.

---

## 7. Motion

- Overlay open/close: 150–200ms ease, fade + 8–12px slide.
- Streaming / uncertainty live: sherbet pulse on a 8px dot (`prefers-reduced-motion`: static).
- No layout jump of Chat width when drawers open.

---

## 8. Implementation phases (build order)

### Phase 0 — Token lock + theme swap (½ day of work, low risk)

- Lock sherbet palette in `zTheme.ts`, workbench theme, `theme.py`, tests.
- Supersede soft-UI hexes if #159 merged; else fold into this workstream.
- **Deliverable:** visual token PR, no layout change.

### Phase 1 — Chat-first shell (highest user impact)

- Composer: Commit Gate pill + queue restyle.
- Hide / stop auto-revealing stacked left views on activate (`openChatOnActivate` stays; remove `workbench.view.extension.z-left` forced open).
- Commands: `z.focusUncertainty` etc. open **WebviewPanel overlays** (or QuickPick → detail) instead of forcing sidebar stack.
- Activity strip keeps two-line behavior; retoken to sherbet.

**Acceptance:** Fresh window = Chat + rail icons only; no five empty panels.

### Phase 2 — Profile overlay (Codex-like)

- Avatar entry + Profile webview page (stats strip, heatmap UI).
- Hover tooltips with **model names + tokens**.
- Backend: extend `usage/summary` or add `usage/activity` with daily buckets `{ date, models: [{ modelId, input, output, requests, cost }] }`.
- Stubs for streaks until real data.

**Acceptance:** Profile matches reference structure in Z palette; hover shows models/tokens.

### Phase 3 — Contextual drawers

- Uncertainty: badge + auto-open on first node; empty = unlit.
- Commit Gate: pill ↔ drawer with `ListRow` + tabs.
- MCP / Skills: overlays; one-time MCP education flag.

**Acceptance:** Empty states never full-height panels; blocked commit auto-opens drawer.

### Phase 4 — Skills `ListRow` + ⌘K overlay; sessions rail (optional)

- Replace Skills cards with `ListRow`.
- Optional session history in rail (local thread list).

### Phase 5 — Seam / branded app chrome (optional)

- Only if Path A/B still feel “VS Code-ish”; apply same tokens to product overlay.

---

## 9. File map (implementation)

| Area | Files |
|------|--------|
| Tokens | `zTheme.ts`, `z-terminal-color-theme.json`, `aider/z/theme.py`, `test_z_ui.py` |
| Shell / activate | `extension.ts`, `package.json` (views/commands), `views.ts` |
| Chat | `chatPanel.ts` (composer pill, queue, overlays host) |
| Profile | `views.ts` ProfileViewProvider → extract `profileView.ts`; usage RPC |
| Usage API | `aider/z/app_server/handlers.py`, `usage_client.py`, `ipc-v0.json` |
| Uncertainty / Gate / MCP / Skills | `uncertaintyView.ts`, `commitGateView.ts`, `mcpView.ts`, `skillsView.ts` — convert to overlay-friendly HTML + `ListRow` CSS module |
| Shared CSS | new `ui.css.ts` or `listRow.ts` fragment injected via `zThemeCss()` |
| Docs | this plan; update `z-soft-ui-design.md` → “superseded by sherbet tokens” |

---

## 10. IPC / data contracts (Profile + heatmap)

### Existing

`usage/summary` → `{ byModel[], total_*, authenticated, range, … }`

### Add (Phase 2)

`usage/activity`:

```json
{
  "range": "year",
  "granularity": "day",
  "days": [
    {
      "date": "2026-07-21",
      "totalTokens": 12000,
      "models": [
        {
          "modelId": "z-composer",
          "inputTokens": 8000,
          "outputTokens": 4000,
          "requests": 3,
          "costUsd": 0.12
        }
      ]
    }
  ]
}
```

Unsigned / no gateway: empty days + `authenticated: false` (no fake heatmap).

---

## 11. Migration & settings

| Setting | Default | Notes |
|---------|---------|--------|
| `z.uiShell` | `chatFirst` | Escape: `legacyStack` restores old five-panel stack for one release |
| `z.openChatOnActivate` | `true` | Keep |
| `z.mcpShowOnboarding` | auto-clear after first open | globalState |

Deprecate forced `workbench.view.extension.z-left` on activate.

---

## 12. Testing plan

| Layer | Tests |
|-------|--------|
| Tokens | `test_z_ui` hex assertions |
| Usage activity | unit: bucket builder; empty when unsigned |
| Profile tooltip | pure function: `formatActivityTooltip(day)` |
| Extension | `tsc`; manual checklist below |
| Gate pill | unit HTML state matrix: clear / blocked N |

**Manual checklist**

- [ ] Reload → only Chat + rail; no Uncertainty empty essay
- [ ] Block a commit → pill turns blocked; drawer opens with `ListRow`
- [ ] Uncertainty node → badge increments; drawer opens once
- [ ] Profile → heatmap; hover cell shows **model id + tokens**
- [ ] Skills/MCP via rail overlay; filters not on Chat
- [ ] Reduced motion: no pulse
- [ ] Narrow width: Chat usable; drawer overlays

---

## 13. Risks & cut lines

| Risk | Mitigation |
|------|------------|
| VS Code forces views visible | Prefer Chat-owned overlays; hide view containers via `when` clauses |
| Heatmap data missing | Ship UI with empty grid + honest empty copy; no fake blues/oranges |
| Scope creep (sessions, Share/Private) | Explicitly Phase 4 / defer |
| Soft-UI PR collision | Rebase; sherbet tokens win |
| Users who liked stacked panels | `z.uiShell: legacyStack` for one version |

**Cut line for first shippable slice:** Phase 0 + Phase 1 + Profile shell (heatmap can be empty UI) → then Phase 2 data.

---

## 14. Suggested PR sequence

1. **Plan** (this doc) — no code  
2. **Tokens sherbet** (Phase 0)  
3. **Chat-first + Commit Gate pill** (Phase 1)  
4. **Profile page + hover + `usage/activity`** (Phase 2)  
5. **Drawers: Uncertainty / Skills / MCP** (Phase 3)  
6. **ListRow unification + Skills ⌘K** (Phase 4)

---

## 15. Open questions (resolve in Phase 0 kickoff)

1. Confirm delta colors: both sherbet family (recommended) vs sage/+ blocked/−.  
2. Profile streaks: stub “—” vs hide until API exists? (**Recommend hide cells that are stub-only.**)  
3. Heatmap timezone: local vs UTC? (**Recommend local.**)  
4. Keep VS Code Activity Bar as rail (Path A) for V1? (**Recommend yes.**)

---

## 16. One-line summary

**Chat is the app; everything else is a badge, pill, or overlay — painted in warm soft-black with a single sherbet signal — and Profile finally shows token activity with model-level hover detail.**
