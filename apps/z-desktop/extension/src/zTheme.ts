/**
 * Z soft terminal palette — comfortable layered blacks + soft burnt orange.
 * Shared by Chat / Uncertainty / Skills / MCP / Commit Gate / Profile webviews.
 * Aligns with aider/z/theme.py.
 */

export const Z_COLORS = {
  background: "#141414",
  surface: "#1C1C1C",
  surfaceRaised: "#262626",
  border: "#333333",
  text: "#EDE8E3",
  textMuted: "#A39E98",
  accent: "#D4894A",
  accentBright: "#E0A06A",
  accentDim: "#8F5A32",
  danger: "#E0A06A", // stay in soft-orange family
  ok: "#A39E98",
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
  --z-radius: 14px;
  --z-radius-sm: 10px;
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
  padding: 8px 14px;
  cursor: pointer;
  font-weight: 600;
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
  background: rgba(212, 137, 74, 0.08);
}
button:disabled { opacity: 0.45; cursor: default; }
textarea, input, select {
  background: var(--z-raised);
  color: var(--z-text);
  border: 1px solid var(--z-border);
  border-radius: var(--z-radius-sm);
}
textarea:focus, input:focus, select:focus {
  outline: none;
  border-color: var(--z-accent);
  box-shadow: 0 0 0 1px rgba(212, 137, 74, 0.35);
}
a { color: var(--z-accent-bright); }
`.trim();
}
