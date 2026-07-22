/**
 * Z sherbet palette — warm soft-black + single soft orange accent.
 * See docs/app/z-ui-redesign-plan.md
 */

export const Z_COLORS = {
  background: "#0E0D0C",
  surface: "#161412",
  surfaceRaised: "#1E1B18",
  border: "#2A2622",
  text: "#F2EDE7",
  textSecondary: "#9C948A",
  textMuted: "#655F58",
  accent: "#F7A56B",
  accentBright: "#F7A56B",
  accentDim: "#C98858",
  accentWash: "rgba(247, 165, 107, 0.08)",
  statusBlocked: "#D97757",
  statusOk: "#8FAE8B",
  danger: "#D97757",
  ok: "#8FAE8B",
} as const;

/** Shared CSS custom properties + base resets for Z webviews. */
export function zThemeCss(): string {
  return `
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
:root {
  --z-bg: ${Z_COLORS.background};
  --z-surface: ${Z_COLORS.surface};
  --z-raised: ${Z_COLORS.surfaceRaised};
  --z-border: ${Z_COLORS.border};
  --z-text: ${Z_COLORS.text};
  --z-secondary: ${Z_COLORS.textSecondary};
  --z-muted: ${Z_COLORS.textMuted};
  --z-accent: ${Z_COLORS.accent};
  --z-accent-bright: ${Z_COLORS.accentBright};
  --z-accent-dim: ${Z_COLORS.accentDim};
  --z-accent-wash: ${Z_COLORS.accentWash};
  --z-status-ok: ${Z_COLORS.statusOk};
  --z-status-blocked: ${Z_COLORS.statusBlocked};
  --z-danger: ${Z_COLORS.danger};
  --z-ok: ${Z_COLORS.ok};
  --z-strip-fg: ${Z_COLORS.textSecondary};
  --z-strip-verb: ${Z_COLORS.text};
  --z-strip-phase: ${Z_COLORS.accent};
  --z-delta-add: ${Z_COLORS.accent};
  --z-delta-del: ${Z_COLORS.accentDim};
  --z-radius: 14px;
  --z-radius-sm: 10px;
  --z-font-ui: "IBM Plex Sans", "Source Sans 3", system-ui, sans-serif;
  --z-font-mono: "IBM Plex Mono", "JetBrains Mono", ui-monospace, monospace;
  color-scheme: dark;
}
html, body {
  background: var(--z-bg);
  color: var(--z-text);
  font-family: var(--z-font-ui);
}
button {
  background: var(--z-accent);
  color: var(--z-bg);
  border: none;
  padding: 8px 14px;
  cursor: pointer;
  font-weight: 600;
  font-family: var(--z-font-ui);
  border-radius: var(--z-radius-sm);
  transition: background 0.15s ease, opacity 0.15s ease;
}
button:hover { background: var(--z-accent-dim); }
button.secondary {
  background: transparent;
  color: var(--z-text);
  border: 1px solid var(--z-border);
  font-weight: 500;
}
button.secondary:hover {
  border-color: var(--z-accent-dim);
  color: var(--z-accent);
  background: var(--z-accent-wash);
}
button:disabled { opacity: 0.45; cursor: default; }
textarea, input, select {
  background: var(--z-raised);
  color: var(--z-text);
  border: 1px solid var(--z-border);
  border-radius: var(--z-radius-sm);
  font-family: var(--z-font-ui);
}
textarea:focus, input:focus, select:focus {
  outline: none;
  border-color: var(--z-accent);
  box-shadow: 0 0 0 1px var(--z-accent-wash);
}
a { color: var(--z-accent); }
.code, .mono, #activity, .msg.tool .bubble, .msg.assistant .bubble {
  font-family: var(--z-font-mono);
}
.list-row {
  display: grid;
  grid-template-columns: 28px 1fr auto;
  gap: 4px 10px;
  padding: 10px 12px;
  border-radius: var(--z-radius-sm);
  cursor: pointer;
  border-left: 2px solid transparent;
}
.list-row:hover { background: var(--z-surface); }
.list-row.selected {
  background: var(--z-accent-wash);
  border-left-color: var(--z-accent);
}
.list-row .glyph {
  width: 20px; height: 20px; position: relative;
  color: var(--z-secondary); font-size: 14px; line-height: 20px; text-align: center;
}
.list-row .glyph .dot {
  position: absolute; right: -2px; bottom: -1px;
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--z-status-blocked);
  box-shadow: 0 0 0 2px var(--z-bg);
}
.list-row .glyph .dot.ok { background: var(--z-status-ok); }
.list-row .title {
  color: var(--z-text); font-size: 13px; font-weight: 500;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.list-row .time { color: var(--z-muted); font-size: 11px; text-align: right; }
.list-row .meta {
  grid-column: 2; color: var(--z-secondary); font-size: 11px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.list-row .diff {
  grid-column: 3; font-family: var(--z-font-mono); font-size: 11px;
  font-variant-numeric: tabular-nums; text-align: right;
}
.list-row .diff .add { color: var(--z-delta-add); }
.list-row .diff .del { color: var(--z-delta-del); margin-left: 6px; }
`.trim();
}
