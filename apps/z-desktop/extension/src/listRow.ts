/**
 * Shared row-based list pattern (spec: "ListRow") — status dot, single-line
 * title, right-aligned time, secondary meta line, optional diff stat.
 * Used by Commit Gate now; Skills reuses it in a later pass.
 */

export type ListRowStatus = "ok" | "blocked" | "neutral";

export interface ListRowData {
  id: string;
  title: string;
  status: ListRowStatus;
  metaLeft?: string;
  time?: string;
  diffAdd?: number;
  diffDel?: number;
  dimmed?: boolean;
}

export interface TabDef {
  id: string;
  label: string;
}

/** CSS for `.list-row` + `.tabs` — include once per webview alongside zThemeCss(). */
export function listRowCss(): string {
  return `
.tabs { display: flex; gap: 4px; margin: 0 0 10px; }
.tabs .tab {
  font-size: 11px; font-weight: 600; padding: 5px 12px; border-radius: 999px;
  cursor: pointer; color: var(--z-text-secondary); background: transparent;
  border: none;
}
.tabs .tab:hover { color: var(--z-text); }
.tabs .tab.active { color: var(--z-text); background: var(--z-raised); }
.list-row {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 8px; border-radius: var(--z-radius-sm);
  cursor: pointer;
}
.list-row:hover { background: var(--z-surface); }
.list-row.selected {
  background: var(--z-accent-wash);
  border-left: 1px solid var(--z-accent);
  padding-left: 7px;
}
.list-row.dimmed { opacity: 0.6; }
.list-row .glyph {
  position: relative; flex: 0 0 auto; width: 20px; height: 20px;
  display: flex; align-items: center; justify-content: center;
  color: var(--z-text-secondary);
}
.list-row .glyph svg { width: 14px; height: 14px; }
.list-row .glyph .dot {
  position: absolute; right: -1px; bottom: -1px;
  width: 7px; height: 7px; border-radius: 50%;
  border: 1.5px solid var(--z-surface);
}
.list-row .glyph .dot.ok { background: var(--z-status-ok); }
.list-row .glyph .dot.blocked { background: var(--z-status-blocked); }
.list-row .body { flex: 1 1 auto; min-width: 0; }
.list-row .top-line { display: flex; align-items: baseline; gap: 8px; }
.list-row .title {
  flex: 1 1 auto; min-width: 0; color: var(--z-text); font-weight: 500;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.list-row .time { flex: 0 0 auto; color: var(--z-muted); font-size: 11px; }
.list-row .meta {
  color: var(--z-text-secondary); font-size: 11.5px; margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.list-row .diff {
  flex: 0 0 auto; font-size: 11px; font-variant-numeric: tabular-nums;
  margin-left: 8px;
}
.list-row .diff .add { color: var(--z-status-ok); }
.list-row .diff .del { color: var(--z-status-blocked); margin-left: 6px; }
.list-row-empty { padding: 24px 8px; color: var(--z-text-secondary); line-height: 1.5; }
`.trim();
}

function escapeHtml(s: string): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** One row. `dataset.id` is set for the caller to wire click handlers. */
export function renderListRow(row: ListRowData, selected = false): string {
  const dot =
    row.status === "neutral"
      ? ""
      : `<span class="dot ${row.status === "ok" ? "ok" : "blocked"}"></span>`;
  const diff =
    row.diffAdd || row.diffDel
      ? `<span class="diff">${row.diffAdd ? `<span class="add">+${row.diffAdd}</span>` : ""}${
          row.diffDel ? `<span class="del">−${row.diffDel}</span>` : ""
        }</span>`
      : "";
  return (
    `<div class="list-row${selected ? " selected" : ""}${row.dimmed ? " dimmed" : ""}" data-id="${escapeHtml(row.id)}">` +
    `<div class="glyph"><svg viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="3.2"/></svg>${dot}</div>` +
    `<div class="body">` +
    `<div class="top-line"><span class="title">${escapeHtml(row.title)}</span>${
      row.time ? `<span class="time">${escapeHtml(row.time)}</span>` : ""
    }${diff}</div>` +
    (row.metaLeft ? `<div class="meta">${escapeHtml(row.metaLeft)}</div>` : "") +
    `</div>` +
    `</div>`
  );
}

export function renderListRowEmpty(text: string): string {
  return `<div class="list-row-empty">${escapeHtml(text)}</div>`;
}

/** `All / Blocked / Cleared`-style pill tabs. */
export function renderTabs(tabs: TabDef[], activeId: string): string {
  return (
    `<div class="tabs">` +
    tabs
      .map(
        (t) =>
          `<button class="tab${t.id === activeId ? " active" : ""}" data-tab="${escapeHtml(t.id)}">${escapeHtml(t.label)}</button>`
      )
      .join("") +
    `</div>`
  );
}
