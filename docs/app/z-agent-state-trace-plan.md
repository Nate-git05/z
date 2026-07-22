# Z — Agent State Indicator & Turn Trace — Implementation Plan

**Date:** 2026-07-22  
**Branch:** `cursor/z-agent-state-trace-plan-313a`  
**Status:** Phase 1+2 implemented on `cursor/z-agent-state-p1-313a` (indicator + turn traces); Phase 3 polish still optional  
**Product:** Z Editor (agent-first Chat)  
**Companion specs:**
- User state/trace visual spec (this plan implements)
- Soft-black / sherbet UI redesign (`docs/app/z-ui-redesign-plan.md`, PR #161)
- Existing activity strip (`docs/app/z-activity-strip-design.md`, S0+S1 shipped)

---

## 0. Problem statement (agreed)

Chat already has a **two-line activity strip** (verbs + counts + phase). That answers *how much* and *which phase bucket* at a glance.

It does **not** answer:

1. **Live ambient narration** — “Contemplating” / “Reading” / “Searching the web” as a quiet row above the streaming reply (sunburst, not another spinner zoo).
2. **Resolved step memory** — a scannable, collapsed log of *what each sub-step was for*, with optional excerpt + Done/Blocked, without dumping raw chain-of-thought into the permanent conversation.

Today’s closest signals:

| Signal | Gap |
|--------|-----|
| Activity strip Line 2 (`Thinking` / `Planning…`) | Top chrome only; not inline with the reply; no sunburst; monospace strip feel |
| `item/agentMessage/delta` | Reasoning tags can leak into the assistant bubble |
| `turn/log` info | Extension **ignores** info-level logs for UI |
| MCP tool bubbles | System lines only; not titled traces with collapse |

**Core principle:** one continuous, low-noise process narrative **inline in Chat** — live indicator for *now*, collapsed turn traces for *just now* — never a side panel and never permanent raw CoT.

---

## 1. Success criteria

1. While the agent is between visible answer tokens / mid-tool, Chat shows a **State indicator** row (sunburst + label) at ~70% opacity, using sherbet only on the icon.
2. One base icon (sunburst) for almost all states; **only search** swaps to a magnifier (crossfade).
3. Slow rotation (3–4s/rev) on the live sunburst only; traces are static / calm.
4. As each reasoning/action step **resolves**, a **Turn trace** row appears: title by default; click expands clock + excerpt + resolution.
5. Finished turns collapse to **title lines only** (stackable); expand on demand.
6. Resolution reuses status language: ✓ Done (`status-ok`) or Blocked / Needs input (`status-blocked`).
7. Trace / indicator copy uses **UI sans** (IBM Plex Sans per redesign) — **not** monospace.
8. Does not replace the activity strip in V1 — they **coexist** with clear jobs (see §3).
9. No fake excerpts: if we lack a human title/excerpt, show a honest short fallback or omit expand body.
10. `prefers-reduced-motion`: sunburst static (no rotation).

---

## 2. Relationship to existing surfaces

```
┌─ Chat ──────────────────────────────────────────────────┐
│  Z  ·  [rail…]                                          │
│  Activity strip (Line1 counts · Line2 phase)  ← KEEP    │
│  ─────────────────────────────────────────────────────  │
│  … prior messages …                                     │
│  You: prompt                                            │
│  ✦ Contemplating                     ← NEW live pill    │
│  Sought clarification on vague request  ▸  ← NEW trace  │
│  Read chatPanel.ts                      ▸               │
│  Z: streamed answer…                                    │
│  composer + gate pill                                   │
└─────────────────────────────────────────────────────────┘
```

| Surface | Job | Density |
|---------|-----|---------|
| **Activity strip** | Macro turn stats (files, searches, +/−, model) | Always under brand while live |
| **State indicator** | Micro “what right now” above the in-flight reply | Ambient, disappears when answer streams |
| **Turn trace** | Per-step titled memory after resolve | Collapsed titles; expand for excerpt |

**Non-goals (this plan):**

- Replacing Uncertainty / Commit Gate panels
- Full tool-call trees / DAG viewers
- Surfacing raw model chain-of-thought permanently in the transcript
- CLI mascot parity (optional later note only)

---

## 3. Visual design (locked to sherbet)

Use redesign tokens (not the older activity-strip burnt-orange hexes):

| Token | Hex | Use |
|-------|-----|-----|
| `bg-base` | `#0E0D0C` | Chat ground |
| `text-primary` | `#F2EDE7` | Contrast reference only |
| `text-secondary` | `#9C948A` | Labels, titles, excerpts |
| `border-subtle` | `#2A2622` | Trace connector |
| `accent-sherbet` | `#F7A56B` | **Live sunburst only** |
| `status-ok` | `#8FAE8B` | Done check |
| `status-blocked` | `#D97757` | Blocked / Needs input |

**Typography:** IBM Plex Sans (or Source Sans 3) — same as Chat chrome. Explicitly **not** Inter-as-default unless already loaded; **not** mono for indicator/trace.

**Opacity:** indicator row ≈ `opacity: 0.7` on the whole row; fades out (≤150ms) when answer deltas arrive.

**Motion:**

1. Sunburst rotate `360deg` in `3.5s` linear infinite while live.
2. Search icon crossfade ~200ms when entering/leaving `searching_web`.
3. Trace expand/collapse height+fade 150–200ms.
4. Reduced motion → no rotate; instant expand.

**Icon assets (new):**

- `media/z-sunburst.svg` — 8-point asterisk/sunburst, currentColor
- `media/z-magnifier.svg` — small search glyph, currentColor

Inline SVG in Chat HTML is fine for V1 (no extra network).

---

## 4. State indicator — detailed spec

### 4.1 Placement

Inline **in the transcript region**, immediately above the current streaming assistant bubble (or where that bubble will appear). Not in the top activity strip.

When no turn is live → not rendered.

When answer text is actively streaming → **hide** indicator (or keep briefly at 0 opacity then remove). Rule: *recede the instant real output starts streaming below it.*

### 4.2 Vocabulary (label carries meaning)

| Internal state id | Label | Icon |
|-------------------|-------|------|
| `contemplating` | Contemplating | sunburst rotate |
| `editing` | Editing | sunburst |
| `searching_web` | Searching the web | magnifier (crossfade) |
| `reading` | Reading | sunburst |
| `running` | Running | sunburst |
| `working` | Working | sunburst (waiting on tool result) |

**Mapping from today’s phases / busy signals:**

| Existing phase / signal | Indicator state |
|-------------------------|-----------------|
| `thinking`, “Waiting for model…”, planning silence | `contemplating` |
| `editing` | `editing` |
| `searching` **and** tool looks like web/search_web | `searching_web` |
| `searching` (codebase/grep) | Prefer label **Searching**? → Spec says web-only for magnifier. **Decision:** codebase search uses sunburst + label **“Searching”** (not magnifier); only web search uses magnifier + “Searching the web”. Add state id `searching` for codebase. |
| file reads / explored | `reading` |
| `running` | `running` |
| `mcp` / tool in flight awaiting result | `working` |
| `waiting` (user input) | **Hide** indicator — waiting UI owns the moment |
| `queued` / idle | Hide |

### 4.3 Implementation approach (UI)

In `chatPanel.ts` webview:

```html
<div id="stateIndicator" class="hidden" aria-live="polite">
  <span class="si-icon sunburst" aria-hidden="true">…svg…</span>
  <span class="si-icon magnifier" aria-hidden="true">…svg…</span>
  <span class="si-label">Contemplating</span>
</div>
```

Drive from `state.indicator: { visible, stateId, label }` posted with Chat state.

---

## 5. Turn trace — detailed spec

### 5.1 Anatomy (expanded)

```
Sought clarification on vague calculus request     ← title (always visible)

 ⏱  The messages are pretty unclear…               ← excerpt
 │
 ✓  Done                                           ← resolution
```

Collapsed default after step completes / turn settles:

```
Sought clarification on vague calculus request  ▸
```

Multiple steps stack as separate title rows for the same turn.

### 5.2 Fields per step

| Field | Required | Notes |
|-------|----------|-------|
| `stepId` | yes | Stable within turn |
| `turnId` | yes | |
| `title` | yes | Human summary of *purpose*, not raw thought |
| `excerpt` | no | ≤ ~280 chars; dim secondary |
| `status` | yes | `running` \| `done` \| `blocked` \| `needs_input` \| `cancelled` |
| `kind` | yes | `thinking` \| `read` \| `search` \| `search_web` \| `edit` \| `shell` \| `mcp` \| `other` |
| `startedAt` / `endedAt` / `durationMs` | no | For future; clock icon is symbolic in V1 |
| `resolutionLabel` | no | Override “Done” / “Blocked” / “Needs input” |

### 5.3 Collapse rules

| Moment | UI |
|--------|-----|
| Step `running` | Optional: show title only with subtle “…” — **or** suppress until done. **Recommend:** do not show running traces; live indicator covers “now”. Emit/update title only on resolve. |
| Step resolves | Append collapsed title; user may expand |
| Turn completes | All steps for that turn remain collapsed |
| New turn starts | Prior turn’s traces stay in transcript history (immutable) |

### 5.4 Click / a11y

- Title row is a `<button>` or `role="button"` with `aria-expanded`
- Expanded region `aria-hidden` when collapsed
- Keyboard: Enter/Space toggles

---

## 6. Data architecture & IPC

### 6.1 Why new notifications

Counters (`turn/activity`) and busy labels (`turn/busy`) cannot carry titles/excerpts/resolutions. Mixing reasoning into `item/agentMessage/delta` pollutes the answer bubble.

### 6.2 Proposed notifications

#### A. `turn/indicator` (optional thin channel)

```json
{
  "turnId": "…",
  "visible": true,
  "state": "contemplating",
  "label": "Contemplating"
}
```

**Alternative (preferred for fewer IPC kinds):** derive indicator client-side from existing `turn/busy` + `turn/activity.phase` + “has assistant delta this turn” flag. Emit `turn/indicator` only if mapping proves too lossy.

**Plan decision:** **Phase 1 = client-side derivation**; add `turn/indicator` in Phase 2 only if needed for web-search distinction / tool-wait nuance.

#### B. `turn/step` (required for traces)

```json
{
  "turnId": "t1",
  "stepId": "s3",
  "kind": "thinking",
  "title": "Sought clarification on vague calculus request",
  "excerpt": "The messages are pretty unclear—they're asking me to think more deeply, but there's no actual problem…",
  "status": "done",
  "resolutionLabel": "Done",
  "durationMs": 4200
}
```

Upsert semantics: same `stepId` may update `running` → `done`.

#### C. Optional `turn/trace/snapshot`

On `turn/completed`, emit full `{ turnId, steps: TurnStep[] }` for reconnect/rebuild. Nice-to-have; Chat can keep in-memory steps if panel retained.

### 6.3 Title & excerpt generation (honesty rules)

| Kind | Title source (priority) | Excerpt source |
|------|-------------------------|----------------|
| thinking | LLM-generated one-liner **or** heuristic from first sentence of reasoning | First ~2 sentences of reasoning (stripped of tags), never dumped into answer bubble |
| read | `Read {basename}` | path list truncated |
| search | `Searched for “{query}”` | hit count if known |
| search_web | `Searched the web for “{query}”` | top result host if known |
| edit | `Edited {N} files` / `Edited {basename}` | file list |
| shell | `Ran {cmd short}` | exit code / first line |
| mcp | `{server}.{tool}` | MCP summary |
| blocked / needs input | Same title; status flips | waiting question subject |

**V1 title strategy (phased):**

1. **Heuristic titles** from tool/log lines + phase (no extra LLM call) — ship first.
2. Optional **cheap title summarizer** later (same model, tiny prompt) behind flag — not required for first PR.

**Never** invent fake reasoning excerpts. If no excerpt → expand shows resolution only.

### 6.4 Emitter wiring (app-server)

| Event | Action |
|-------|--------|
| `llm_started` / thinking phase | Indicator → contemplating (client); open thinking step id optional |
| Reasoning stream (base_coder) | Buffer into **pending thinking step**; **do not** send as `item/agentMessage/delta` when tagged as reasoning |
| Answer stream starts | Close thinking step (`done` + title/excerpt); hide indicator |
| `observe_tool_output` / apply hooks | Close prior step; open kind-specific step; on tool end → `turn/step` done |
| MCP `tool_started/finished/error` | Map to `turn/step` (in addition to or instead of system bubbles — **decision:** keep short system tool lines **or** replace with traces; prefer **traces replace MCP system bubbles** once traces ship to avoid double UI) |
| `turn/waiting_input` | Prior step → `needs_input` / `blocked` as appropriate |
| `turn/completed` / error | Finalize open steps (`cancelled` if interrupted); snapshot optional |

Primary files:

- `aider/z/app_server/activity.py` — extend or sibling `TurnTraceTracker`
- `aider/z/app_server/io_bridge.py` — emit steps; split reasoning deltas
- `aider/z/app_server/turn_runner.py` — finalize on complete
- `aider/z/mcp_turn.py` — step mapping
- `aider/coders/base_coder.py` — route reasoning away from answer delta when app-server bridge present

Protocol: `apps/z-desktop/protocol/ipc-v0.json`

---

## 7. Extension / Chat rendering

### 7.1 State model (`chatPanel.ts`)

```ts
type IndicatorState =
  | { visible: false }
  | { visible: true; stateId: string; label: string; icon: "sunburst" | "magnifier" };

interface TraceStep {
  stepId: string;
  turnId: string;
  title: string;
  excerpt?: string;
  status: "done" | "blocked" | "needs_input" | "cancelled";
  kind: string;
  resolutionLabel?: string;
  expanded?: boolean; // UI-only
}

// messages[] gains optional kind "trace" OR parallel tracesByTurn: Map
```

**Recommend:** store traces as synthetic transcript items:

```ts
{ id, role: "system", kind: "trace", step: TraceStep }
```

so they scroll with the conversation and persist for the panel lifetime. Do **not** write them into coder history / markdown chat history in V1 (display-only).

### 7.2 CSS

New rules in Chat `html()` (and shared tokens already in `zTheme.ts`):

- `#stateIndicator` flex row, gap 8px, opacity 0.7, font-ui 13px, color secondary
- `.si-icon.sunburst` 16px, color accent, animation rotate
- `.trace` title button; `.trace-body` with connector; icons clock / check

### 7.3 Coexistence with activity strip

Keep strip as-is (sherbet-retokened via redesign). Indicator does not duplicate Line 2 copy word-for-word always — Contemplating ≈ Thinking, but indicator is **inline / ambient**; strip stays **macro**.

If redesign PR #161 is not yet on `main`, implement tokens against whatever is merged; do not reintroduce burnt-orange `#C96A2B` for these components.

---

## 8. Implementation phases

### Phase 0 — Spec lock & fixtures (this PR)

- Land this plan doc.
- Resolve open questions §12.
- Add fixture JSON examples under `docs/app/fixtures/turn-trace-examples.json` (optional in plan PR or first impl PR).

### Phase 1 — State indicator only (UI + client mapping)

**Status:** Done (`cursor/z-agent-state-p1-313a`, extension 0.9.1)

**Scope:** Chat webview indicator; map from existing `turn/busy` + `turn/activity` + streaming flag.

**Deliverable:** Sunburst + Contemplating/Editing/Reading/Running/Working/Searching; magnifier for web if detectable else codebase “Searching”; hide on answer delta / wait / idle.

**No new IPC required** if web detection is weak — magnifier can wait for Phase 2 tool kinds.

**Acceptance:**

- [x] Busy thinking → Contemplating + rotating sunburst
- [x] Answer streams → indicator gone
- [x] Reduced motion → no spin
- [x] Opacity ~70%; sans font

### Phase 2 — `turn/step` pipeline (heuristic titles)

**Status:** Done (`cursor/z-agent-state-p1-313a`, extension 0.9.2)

**Scope:** `TurnTraceTracker`, emit on tool/MCP/thinking close; Chat renders collapsed traces; expand excerpt + Done.

**Acceptance:**

- [x] Multi-step turn shows stacked titles
- [x] Expand shows connector + excerpt (when present) + ✓ Done
- [x] Waiting input can mark Needs input / Blocked
- [x] No raw CoT in assistant bubble (reasoning diverted)

### Phase 3 — Polish & replace noisy system tool lines

- MCP/system tool bubbles → traces (or shorten to one line)
- Search web detection
- Snapshot on complete / reconnect
- Optional title summarizer behind `Z_TRACE_TITLES=llm`

### Phase 4 — Optional CLI parity

- Not required; terminal already has mascot/spinner — leave unless product asks

---

## 9. File map

| Area | Files |
|------|--------|
| Plan / fixtures | `docs/app/z-agent-state-trace-plan.md`, optional fixtures |
| Protocol | `apps/z-desktop/protocol/ipc-v0.json` |
| Trace tracker | `aider/z/app_server/activity.py` or new `turn_trace.py` |
| Emit / reasoning split | `io_bridge.py`, `base_coder.py`, `turn_runner.py`, `mcp_turn.py` |
| Chat UI | `apps/z-desktop/extension/src/chatPanel.ts` |
| Tokens | `zTheme.ts` (reuse; add `--z-indicator-opacity` if useful) |
| Icons | inline SVG or `media/z-sunburst.svg` |
| Tests | `tests/basic/test_z_activity_strip.py` extend; new `test_z_turn_trace.py`; extension compile |
| Activity strip doc | Cross-link from `z-activity-strip-design.md` (“companion: state indicator + turn trace”) |

---

## 10. Testing plan

| Layer | Cases |
|-------|--------|
| Unit tracker | thinking open/close → one done step; tool sequence titles; unsigned excerpt empty |
| Unit map | phase → indicator stateId / label / icon |
| IPC | `turn/step` shape in handler/notify tests |
| Reasoning split | tagged reasoning does not appear in `item/agentMessage/delta` when bridge active |
| Extension | `tsc`; manual checklist below |
| a11y | expand toggles aria-expanded; reduced motion |

**Manual checklist**

- [ ] Prompt that causes long think → Contemplating appears above reply slot
- [ ] First answer token → indicator vanishes
- [ ] File read / edit / shell → collapsed titles appear
- [ ] Expand → clock, excerpt, Done
- [ ] Blocked / plan confirm → Needs input styling
- [ ] Multi-step turn → stack of titles, scannable
- [ ] Activity strip still updates counts/+−

---

## 11. Risks & cut lines

| Risk | Mitigation |
|------|------------|
| Double UI (strip + indicator + traces) feels noisy | Indicator only while no answer tokens; traces collapsed by default; strip stays macro |
| Reasoning still leaks into bubbles | Hard split in bridge before Phase 2 ships traces |
| Title quality low with heuristics | Honest fallbacks (“Thought”, “Ran command”); LLM titles Phase 3 only |
| Web search vs codebase search confusion | Separate labels; magnifier **only** for web |
| Redesign tokens not on main yet | Depend on / rebase onto UI redesign; sherbet hexes hardcoded if needed |
| Performance of many steps | Cap stored steps per turn (e.g. 40); collapse older |

**Cut line for first shippable slice:** Phase 1 (indicator) alone is valuable. Phase 2 is the full companion product.

---

## 12. Open questions (resolve at Phase 0 kickoff)

1. **Keep activity strip Line 2** once Contemplating exists? (**Recommend yes** — different locus.)
2. **Codebase search label:** “Searching” (sunburst) vs reuse “Searching the web” only for web — (**Recommend split**, §4.2.)
3. **Show running traces** or only resolved? (**Recommend resolved-only.**)
4. **Replace MCP system bubbles** with traces in same PR as Phase 2? (**Recommend yes** once traces stable.)
5. **Persist traces** to chat history markdown? (**Recommend no** for V1 — panel memory only.)
6. **Font:** spec mentions Inter; redesign locked IBM Plex Sans — (**Recommend Plex**, match redesign.)

---

## 13. Suggested PR sequence

1. **Plan** (this doc) — no product code  
2. **Phase 1** — State indicator UI + client mapping  
3. **Phase 2a** — `turn/step` + heuristic tracker + Chat traces  
4. **Phase 2b** — Reasoning divert from answer deltas  
5. **Phase 3** — Polish, web search icon, optional LLM titles  

---

## 14. One-line summary

**Live sunburst narrates *now*; collapsed titled traces remember *just then* — inline in Chat, sherbet-quiet, never a CoT dump.**
