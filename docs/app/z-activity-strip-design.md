# Z Activity Strip — Design Doc (orange agent status)

**Date:** 2026-07-22  
**Branch:** `cursor/z-activity-strip-design-313a`  
**Status:** Design only — not implemented  
**Surface:** Center Chat (Z Editor) + optional CLI parity later  
**Look:** Z Terminal — near-black `#0A0A0A`, text `#F5F5F5`, accent `#C96A2B` / bright `#E07830`, muted `#D8D8D8`  
**Inspiration:** Compact Cursor-style activity line (editing / exploring / searches / commands + +/- deltas + “Planning next moves”)

---

## 1. Problem

Today Chat shows a single flat `busyLabel` (“Working…”, “Waiting — …”). That hides:

- what the agent is doing (editing vs searching vs planning)
- which model is active / being chosen
- how much code changed (+ / −)
- thinking vs planning vs applying

Users need a **dense, glanceable status strip** under the brand / above the transcript — same information architecture as the reference UI, but **Z orange**, not green/red Chrome chrome.

---

## 2. Goals

1. One composition: a thin **two-line activity strip** (not a dashboard, not cards).
2. Brand-safe: orange accent for verbs and active phase; no purple, no glow, no pill farms.
3. Live: updates from existing IPC (`turn/busy`, `turn/log`, future richer events) without reflowing the whole chat.
4. Mobile-friendly: wraps to two lines; never steals the hero brand “Z”.

### Non-goals

- Full timeline / tool call tree (Commit Gate / Uncertainty stay separate).
- Green/red semantic diffs as primary chrome (Z uses orange + muted; see §5).
- Blocking the composer with a modal “thinking” overlay.

---

## 3. Placement

```
┌─ Chat panel ─────────────────────────────────────┐
│  Z                          ← brand (unchanged)  │
│  ┌─ Activity strip ───────────────────────────┐  │
│  │ Line 1: verbs + counts + Δ                 │  │
│  │ Line 2: phase (Thinking / Planning / …)    │  │
│  └────────────────────────────────────────────┘  │
│  [optional reconnect banner]                     │
│  messages…                                       │
│  waiting_input / queue                           │
│  composer (sticky)                               │
└──────────────────────────────────────────────────┘
```

- Strip sits **directly under `#brand`**, replacing/augmenting `#status`.
- Idle: single muted line — `Agent ready — you prompt, Z programs` (current copy OK).
- Busy: two-line strip as below.

---

## 4. Information architecture

### Line 1 — Activity summary (what happened / is happening)

Pattern (reference → Z):

> `Editing 14 files, explored 1 file, 2 searches, ran 2 commands` `+221` `−40`

**Tokens (ordered, omit zeros):**

| Token | When | Example |
|-------|------|---------|
| Editing *N* files | Files written/patched this turn | `Editing 3 files` |
| Explored *N* files | Reads / z-tool read|glob | `explored 2 files` |
| *N* searches | grep / codebase search | `4 searches` |
| Ran *N* commands | shell / tests | `ran 1 command` |
| Called *N* MCP tools | mcp/tool_* | `2 MCP tools` |
| Choosing model | Router selecting / escalating | `Choosing model` |
| Using *model* | After route known | `Using z-composer` |

**Delta badges (end of line 1):**

| Badge | Meaning | Z color |
|-------|---------|---------|
| `+N` | Lines added this turn | `#E07830` (accent bright) |
| `−N` | Lines removed this turn | `#C96A2B` at ~70% opacity (same family, not traffic-light red) |

No green. No system red. Hierarchy = bright orange (add) vs dimmer orange (delete).

### Line 2 — Phase (cognitive state)

Single short phrase, sentence case, no ellipsis spam:

| Phase id | Copy | When |
|----------|------|------|
| `thinking` | Thinking | Waiting on model / streaming silence |
| `planning` | Planning next moves | Plan mode / plan_confirm pending / explore |
| `editing` | Applying edits | SEARCH/REPLACE / write in flight |
| `searching` | Searching | tool-loop / grep burst |
| `running` | Running commands | shell / tests |
| `mcp` | Using tools | MCP call in flight |
| `choosing_model` | Choosing model | gateway route / escalate |
| `waiting` | Waiting for you | `turn/waiting_input` |
| `queued` | Queued follow-up | queueLen > 0 while busy |

Line 2 uses **accent** (`#C96A2B`) when busy; muted when idle.

Optional micro-motion (2–3 max, intentional):

1. Soft opacity pulse on Line 2 verb (1.2s, ease-in-out) while busy — not a spinner bar.
2. Delta badges fade-in when first non-zero.
3. Line 1 count ticks use a short number transition (no bounce).

---

## 5. Visual design (orange)

### Tokens (CSS variables — align `zTheme.ts`)

```css
--z-bg: #0A0A0A;
--z-text: #F5F5F5;
--z-muted: #D8D8D8;
--z-accent: #C96A2B;
--z-accent-bright: #E07830;
--z-strip-fg: #D8D8D8;          /* Line 1 body */
--z-strip-verb: #F5F5F5;        /* first verb “Editing” */
--z-strip-phase: #C96A2B;       /* Line 2 */
--z-delta-add: #E07830;
--z-delta-del: rgba(201, 106, 43, 0.72);
```

### Typography

- Same mono stack as Chat: IBM Plex Mono / JetBrains Mono.
- Line 1: 12px, weight 400; verb weight 500–600.
- Line 2: 12px, weight 500, accent color.
- Deltas: 11px tabular-nums, no pills, no borders — just colored text with 8px gap.

### Layout

```
#activity {
  padding: 0 20px 10px;
  min-height: 2.6em;
}
#activity .line1 { color: var(--z-strip-fg); }
#activity .line1 .verb { color: var(--z-strip-verb); }
#activity .deltas { float/inline-flex; gap: 8px; margin-left: 10px; }
#activity .line2 { color: var(--z-strip-phase); margin-top: 2px; }
```

**No cards, no chips, no left border accent bar** (avoid reverse-highlight status bars from CLI). Strip is typography-only on the atmospheric Chat background.

### Idle vs busy

| State | Line 1 | Line 2 |
|-------|--------|--------|
| Idle / connected | `Agent ready — you prompt, Z programs` | hidden |
| Disconnected | `Z · disconnected` (muted) | optional reconnect hint |
| Busy | summary + deltas | phase copy |
| Waiting input | summary frozen | `Waiting for you` |

---

## 6. Data model (extension)

```ts
interface ActivityStripState {
  phase: 
    | "idle" | "thinking" | "planning" | "editing"
    | "searching" | "running" | "mcp" | "choosing_model"
    | "waiting" | "queued";
  modelId?: string | null;
  editingFiles: number;
  exploredFiles: number;
  searches: number;
  commands: number;
  mcpCalls: number;
  linesAdded: number;
  linesRemoved: number;
  // optional detail for tooltip / a11y
  fileNames?: string[];
}
```

### IPC (minimal — prefer reuse, then extend)

**Reuse now:**

| Event | Mapping |
|-------|---------|
| `turn/busy` `{ phase, label, state }` | Map known phases → Line 2; fallback Thinking |
| `turn/started` | Reset counters for turn |
| `turn/completed` / `turn/error` | → idle (keep last summary ~1.5s then clear) |
| `mcp/tool_started\|finished` | mcpCalls++, phase=`mcp` |
| `turn/waiting_input` | phase=`waiting` |
| `turn/queued` | phase=`queued` if busy |

**Add (Phase A of impl):**

| Notification | Payload |
|--------------|---------|
| `turn/activity` | `{ turnId, editingFiles, exploredFiles, searches, commands, linesAdded, linesRemoved, modelId?, phase? }` |

Emitted from `turn_runner` / `AppServerIO` when:

- edits applied (count files + line deltas from apply_updates)
- z-tool / grep runs
- shell runs
- routing selects model (`z_routing.model_id`)

**Throttle:** coalesce to ≤10 Hz to Chat (same spirit as delta throttle).

---

## 7. Copy rules

- Prefer gerunds: Editing / Exploring / Choosing / Planning.
- Counts always Arabic numerals; pluralize correctly (`1 file` / `2 files`).
- Never show `+0` / `−0`.
- Model id shortened: strip provider prefix (`openai/gpt-4o` → `gpt-4o`, `z-composer` stays).
- Max Line 1 length ~90 chars; if overflow, drop least-important tokens (explored → searches → commands) before dropping Editing.

---

## 8. States walkthrough (examples)

**Thinking after send**

```
Thinking
```
(Line 1 empty or `Using z-composer` if known)

**Planning**

```
explored 3 files, 2 searches
Planning next moves
```

**Editing with diffs**

```
Editing 14 files, explored 1 file, 2 searches, ran 2 commands   +221  −40
Applying edits
```

**Choosing / escalating model**

```
Choosing model
```
then

```
Using z-sonnet
Thinking
```

**MCP**

```
Editing 1 file, 1 MCP tool   +12  −3
Using tools
```

---

## 9. Accessibility

- Strip is `aria-live="polite"` (not assertive — avoid fighting screen readers during stream).
- Deltas announced as “plus 221, minus 40”.
- Phase changes don’t steal focus from composer.
- Respect `prefers-reduced-motion`: disable pulse.

---

## 10. Implementation slices (when building)

| Slice | Work |
|-------|------|
| **S0** | Chat CSS/HTML: replace `#status` with `#activity` two-line layout; drive from local `ActivityStripState` mapped from current `busyLabel` only (no backend yet) |
| **S1** | Emit `turn/activity` from app-server (edit/search/shell counters + line deltas) |
| **S2** | Model id from gateway / turn_runner into strip |
| **S3** | CLI status line parity (`theme.py` colors) — optional |

**Suggested first PR:** S0 + S1 (visible product win).

---

## 11. File map (future)

| File | Role |
|------|------|
| `apps/z-desktop/extension/src/chatPanel.ts` | Render strip, handle `turn/activity` |
| `apps/z-desktop/extension/src/zTheme.ts` | Strip CSS vars |
| `aider/z/app_server/io_bridge.py` / `turn_runner.py` | Emit activity |
| `aider/z/turn_ux.py` | Optional phase vocabulary align |
| `apps/z-desktop/protocol/ipc-v0.json` | Document `turn/activity` |

---

## 12. Acceptance checklist

- [ ] Busy turn shows two-line orange strip (not a single “Working…”).
- [ ] Editing + searches + commands + deltas update live.
- [ ] Phase copy includes Thinking / Planning next moves / Choosing model / Applying edits.
- [ ] No green/red badges; orange family only.
- [ ] Idle restores ready copy; no leftover deltas after turn settles.
- [ ] Desktop + narrow Chat width: Line 1 wraps without overlapping composer.

---

## 13. Open questions

1. Keep last-turn summary visible for ~1.5s after complete, or clear immediately?
2. Show file name chips on hover only, or never (prefer never for V1 — density)?
3. Should Uncertainty upserts appear as a token (`1 risk flagged`) — probably **no** for V1 (separate panel).

---

## 14. Success statement

When the agent works, Chat looks like the reference: a quiet charcoal strip, **orange** verbs and phase line, compact `+N −N` — so “editing / choosing model / thinking / planning / deletions” are readable at a glance without turning Chat into a dashboard.
