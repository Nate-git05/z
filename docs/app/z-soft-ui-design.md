# Z Soft UI — Comfortable black + soft orange

**Date:** 2026-07-22  
**Branch:** `cursor/z-soft-ui-313a`  
**Status:** Implementing (theme + Chat chrome)  
**Inspiration:** Soft layered dark agent UIs (depth without harsh pure black)  
**Brand:** Still Z — orange + black, never purple / cream / terracotta poster looks  

---

## 1. Goal

Make Z Editor feel **smooth and comfortable**:

- Blacks are **layered** (workspace / sidebar / raised / input), not a flat `#000` / harsh `#0A0A0A`
- Orange is **softer** — peach-burnt, lower saturation — so it sits in the dark instead of neon-clashing
- Generous radius + breathing room on interactive surfaces (composer, waiting, queue)
- Keep agent-first layout (Chat center; left management; Commit Gate)

### Non-goals

- Copy Codex layout 1:1 (no purple Plus chip, no four marketing cards in the hero)
- Glow stacks, pill farms, dashboard chrome in the first viewport
- Changing agent architecture / IPC

---

## 2. Palette (soft Z Terminal)

| Token | Hex | Role |
|-------|-----|------|
| `--z-bg` | `#141414` | Deep workspace (comfortable black) |
| `--z-surface` | `#1C1C1C` | Sidebar / composer shelf |
| `--z-raised` | `#262626` | Inputs, selected rows, panels |
| `--z-border` | `#333333` | Hairline separators (low contrast) |
| `--z-text` | `#EDE8E3` | Warm off-white body |
| `--z-muted` | `#A39E98` | Secondary / status |
| `--z-accent` | `#D4894A` | Soft burnt orange (brand) |
| `--z-accent-bright` | `#E0A06A` | Hover / active / deltas + |
| `--z-accent-dim` | `#8F5A32` | Idle icons / dim phase |

**Rule:** Additions and deletions stay orange-family (`bright` / `accent`) — never green/red.

---

## 3. Surfaces & motion

1. **Depth:** bg < surface < raised (three blacks minimum).
2. **Radius:** composer / waiting / queue ≈ 14px; buttons ≈ 10px; no sharp chrome.
3. **Borders:** prefer tone-on-tone (`#333`) over bright orange outlines; orange for focus/active only.
4. **Motion:** keep activity-strip phase pulse; prefer opacity over bounce.
5. **Hero budget:** Chat first viewport = brand **Z** + activity strip + transcript + composer — no card grid.

---

## 4. File map

| File | Change |
|------|--------|
| `apps/z-desktop/extension/src/zTheme.ts` | Soft tokens + base control radius |
| `apps/z-desktop/extension/themes/z-terminal-color-theme.json` | Workbench soft blacks / soft orange |
| `apps/z-desktop/extension/src/chatPanel.ts` | Composer / waiting / queue softness |
| `aider/z/theme.py` | CLI parity tokens |
| `docs/app/z-activity-strip-design.md` | Point at soft tokens (optional note) |

---

## 5. Acceptance

- [ ] Chat + side panels use layered blacks (visible depth between pane / input)
- [ ] Accent reads soft orange, not neon
- [ ] `+N` / `−N` still both orange
- [ ] No purple introduced
- [ ] Extension `tsc` clean; `test_z_ui` updated for new accent hex
