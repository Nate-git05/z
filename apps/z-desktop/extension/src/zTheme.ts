/**
 * Z "soft black / sherbet" palette — warm near-black + desaturated soft
 * orange, humanist-sans UI chrome with monospace reserved for code/tool
 * output. Shared by Chat / Uncertainty / Skills / MCP / Commit Gate /
 * Profile webviews. Aligns with aider/z/theme.py.
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
  accentBright: "#FBBE94",
  accentDim: "#C98858",
  accentWash: "rgba(247, 165, 107, 0.08)",
  statusOk: "#8FAE8B",
  statusBlocked: "#D97757",
} as const;

/** Shared CSS custom properties + base resets for Z webviews. */
export function zThemeCss(): string {
  return `
:root {
  --z-bg: ${Z_COLORS.background};
  --z-surface: ${Z_COLORS.surface};
  --z-raised: ${Z_COLORS.surfaceRaised};
  --z-border: ${Z_COLORS.border};
  --z-text: ${Z_COLORS.text};
  --z-text-secondary: ${Z_COLORS.textSecondary};
  --z-muted: ${Z_COLORS.textMuted};
  --z-accent: ${Z_COLORS.accent};
  --z-accent-bright: ${Z_COLORS.accentBright};
  --z-accent-dim: ${Z_COLORS.accentDim};
  --z-accent-wash: ${Z_COLORS.accentWash};
  --z-status-ok: ${Z_COLORS.statusOk};
  --z-status-blocked: ${Z_COLORS.statusBlocked};
  --z-strip-fg: ${Z_COLORS.textSecondary};
  --z-strip-verb: ${Z_COLORS.text};
  --z-strip-phase: ${Z_COLORS.accent};
  --z-delta-add: ${Z_COLORS.statusOk};
  --z-delta-del: ${Z_COLORS.statusBlocked};
  --z-font-ui: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --z-font-mono: "IBM Plex Mono", "JetBrains Mono", "SF Mono", ui-monospace, monospace;
  --z-radius: 14px;
  --z-radius-sm: 10px;
  color-scheme: dark;
}
html, body {
  background: var(--z-bg);
  color: var(--z-text);
  font-family: var(--z-font-ui);
  line-height: 1.5;
}
button {
  background: var(--z-accent);
  color: var(--z-bg);
  border: none;
  padding: 8px 16px;
  cursor: pointer;
  font-weight: 600;
  font-family: var(--z-font-ui);
  border-radius: var(--z-radius-sm);
  transition: background 0.15s ease, opacity 0.15s ease;
}
button:hover { background: var(--z-accent-bright); }
button.secondary {
  background: transparent;
  color: var(--z-text);
  border: 1px solid var(--z-border);
  font-weight: 500;
}
button.secondary:hover {
  border-color: var(--z-accent);
  color: var(--z-accent-bright);
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
a { color: var(--z-accent-bright); }
code, pre, .mono { font-family: var(--z-font-mono); }
`.trim();
}
