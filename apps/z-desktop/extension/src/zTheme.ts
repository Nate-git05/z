/**
 * Z terminal palette — same tokens as aider/z/theme.py (orange + near-black).
 * Injected into every Z webview so Chat / Uncertainty / Skills / Commit Gate match the CLI.
 */

export const Z_COLORS = {
  background: "#0A0A0A",
  surface: "#121212",
  surfaceRaised: "#1A1A1A",
  border: "#2A2A2A",
  text: "#F5F5F5",
  textMuted: "#D8D8D8",
  accent: "#C96A2B",
  accentBright: "#E07830",
  accentDim: "#8A4A1E",
  danger: "#E07830", // stay in-palette; bright orange for high risk
  ok: "#D8D8D8",
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
  --z-muted: ${Z_COLORS.textMuted};
  --z-accent: ${Z_COLORS.accent};
  --z-accent-bright: ${Z_COLORS.accentBright};
  --z-accent-dim: ${Z_COLORS.accentDim};
  --z-danger: ${Z_COLORS.danger};
  --z-ok: ${Z_COLORS.ok};
  --z-strip-fg: ${Z_COLORS.textMuted};
  --z-strip-verb: ${Z_COLORS.text};
  --z-strip-phase: ${Z_COLORS.accent};
  --z-delta-add: ${Z_COLORS.accentBright};
  --z-delta-del: ${Z_COLORS.accent};
  color-scheme: dark;
}
html, body {
  background: var(--z-bg);
  color: var(--z-text);
}
button {
  background: var(--z-accent);
  color: var(--z-bg);
  border: none;
  padding: 6px 12px;
  cursor: pointer;
  font-weight: 600;
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
}
button:disabled { opacity: 0.45; cursor: default; }
textarea, input, select {
  background: var(--z-raised);
  color: var(--z-text);
  border: 1px solid var(--z-border);
}
textarea:focus, input:focus, select:focus {
  outline: 1px solid var(--z-accent);
  border-color: var(--z-accent);
}
a { color: var(--z-accent-bright); }
`.trim();
}
