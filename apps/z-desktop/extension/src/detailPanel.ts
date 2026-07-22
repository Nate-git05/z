/**
 * Commit / PR detail view — Summary / Timeline / Code tabs, opened as its
 * own editor-area tab (mirrors MainChatPanel's pattern: rich diff content
 * needs real width, not the sidebar). Multi-instance, keyed by id, so
 * reopening the same item reveals its existing tab instead of duplicating.
 */

import * as vscode from "vscode";
import { zThemeCss } from "./zTheme";
import { renderDiffHtml, diffCss } from "./diffRenderer";

export type DetailKind = "commit" | "pr";

export interface DetailSummaryRow {
  label: string;
  value: string;
}

export interface DetailTimelineEvent {
  title: string;
  detail?: string;
  time?: string;
  status?: "ok" | "blocked" | "neutral";
}

export interface DetailData {
  id: string;
  kind: DetailKind;
  title: string;
  subtitle: string;
  state?: string;
  summaryRows: DetailSummaryRow[];
  description?: string;
  checks?: Array<{ name: string; conclusion?: string | null }>;
  timeline: DetailTimelineEvent[];
  diff: string;
  externalUrl?: string;
}

function escapeHtml(s: string): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export class DetailPanelManager {
  private panels = new Map<string, vscode.WebviewPanel>();

  constructor(private readonly context: vscode.ExtensionContext) {}

  open(data: DetailData): void {
    const existing = this.panels.get(data.id);
    if (existing) {
      existing.reveal(vscode.ViewColumn.Active, false);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "z.detailPanel",
      data.title.length > 48 ? `${data.title.slice(0, 47)}…` : data.title,
      { viewColumn: vscode.ViewColumn.Active, preserveFocus: false },
      { enableScripts: true, localResourceRoots: [this.context.extensionUri] }
    );
    panel.webview.html = this.html(data);
    panel.webview.onDidReceiveMessage((msg) => {
      if (msg?.type === "openExternal" && data.externalUrl) {
        void vscode.env.openExternal(vscode.Uri.parse(data.externalUrl));
      }
    });
    panel.onDidDispose(() => this.panels.delete(data.id));
    this.panels.set(data.id, panel);
  }

  private html(data: DetailData): string {
    const rows = data.summaryRows
      .map(
        (r) =>
          `<div class="srow"><div class="slabel">${escapeHtml(r.label)}</div><div class="svalue">${escapeHtml(r.value)}</div></div>`
      )
      .join("");

    const checksHtml = (data.checks || [])
      .map((c) => {
        const ok = c.conclusion === "success";
        const failed = c.conclusion === "failure" || c.conclusion === "cancelled" || c.conclusion === "timed_out";
        const cls = ok ? "ok" : failed ? "blocked" : "neutral";
        const label = c.conclusion ? c.conclusion.replace(/_/g, " ") : "pending";
        return `<div class="check-row"><span class="dot ${cls}"></span><span class="check-name">${escapeHtml(c.name)}</span><span class="check-label ${cls}">${escapeHtml(label)}</span></div>`;
      })
      .join("");

    const timelineHtml = data.timeline
      .map((ev) => {
        const cls = ev.status || "neutral";
        return (
          `<div class="tl-item">` +
          `<div class="tl-dot ${cls}"></div>` +
          `<div class="tl-body">` +
          `<div class="tl-title">${escapeHtml(ev.title)}${ev.time ? `<span class="tl-time">${escapeHtml(ev.time)}</span>` : ""}</div>` +
          (ev.detail ? `<div class="tl-detail">${escapeHtml(ev.detail)}</div>` : "") +
          `</div></div>`
        );
      })
      .join("");

    return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  ${zThemeCss()}
  ${diffCss()}
  html, body { height: 100%; margin: 0; padding: 0; }
  #app { max-width: 900px; margin: 0 auto; padding: 0 24px 40px; }
  #hdr { padding: 24px 0 12px; }
  #hdr .title { font-size: 22px; font-weight: 700; color: var(--z-text); line-height: 1.3; }
  #hdr .subtitle { margin-top: 6px; color: var(--z-text-secondary); font-size: 12.5px; }
  #tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--z-border); margin: 16px 0 20px; }
  #tabs .tab {
    background: transparent; border: none; color: var(--z-text-secondary);
    font-weight: 600; font-size: 12.5px; padding: 8px 4px; margin-right: 18px;
    cursor: pointer; border-bottom: 2px solid transparent;
  }
  #tabs .tab.active { color: var(--z-text); border-bottom-color: var(--z-accent); }
  .pane { display: none; }
  .pane.active { display: block; }
  .srow { display: flex; gap: 14px; padding: 7px 0; border-bottom: 1px solid var(--z-border); font-size: 12.5px; }
  .slabel { flex: 0 0 130px; color: var(--z-text-secondary); }
  .svalue { flex: 1 1 auto; color: var(--z-text); }
  .desc { margin: 18px 0; line-height: 1.6; font-size: 13px; color: var(--z-text); white-space: pre-wrap; }
  .sec-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--z-text-secondary); margin: 22px 0 8px; }
  .check-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; font-size: 12.5px; }
  .check-row .dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
  .dot.ok { background: var(--z-status-ok); }
  .dot.blocked { background: var(--z-status-blocked); }
  .dot.neutral { background: var(--z-muted); }
  .check-name { flex: 1 1 auto; color: var(--z-text); }
  .check-label { text-transform: capitalize; font-size: 11px; }
  .check-label.ok { color: var(--z-status-ok); }
  .check-label.blocked { color: var(--z-status-blocked); }
  .check-label.neutral { color: var(--z-text-secondary); }
  .tl-item { display: flex; gap: 12px; padding: 0 0 18px; position: relative; }
  .tl-item:not(:last-child)::before {
    content: ''; position: absolute; left: 3px; top: 14px; bottom: -4px; width: 1px; background: var(--z-border);
  }
  .tl-dot { flex: 0 0 auto; width: 7px; height: 7px; margin-top: 5px; border-radius: 50%; background: var(--z-muted); }
  .tl-dot.ok { background: var(--z-status-ok); }
  .tl-dot.blocked { background: var(--z-status-blocked); }
  .tl-title { font-size: 12.5px; color: var(--z-text); }
  .tl-time { color: var(--z-muted); font-size: 11px; margin-left: 8px; }
  .tl-detail {
    margin-top: 6px; padding: 10px 12px; border: 1px solid var(--z-border); background: var(--z-surface);
    border-radius: var(--z-radius-sm); font-size: 12px; color: var(--z-text-secondary); white-space: pre-wrap;
    max-height: 160px; overflow-y: auto;
  }
  #openExternal { margin-top: 4px; }
</style>
</head>
<body>
  <div id="app">
    <div id="hdr">
      <div class="title">${escapeHtml(data.title)}</div>
      <div class="subtitle">${escapeHtml(data.subtitle)}</div>
      ${data.externalUrl ? `<button class="secondary" id="openExternal">Open on GitHub ↗</button>` : ""}
    </div>
    <div id="tabs">
      <button class="tab active" data-tab="summary">Summary</button>
      <button class="tab" data-tab="timeline">Timeline</button>
      <button class="tab" data-tab="code">Code</button>
    </div>
    <div class="pane active" id="pane-summary">
      ${rows}
      ${data.description ? `<div class="desc">${escapeHtml(data.description)}</div>` : ""}
      ${checksHtml ? `<div class="sec-title">Checks</div>${checksHtml}` : ""}
    </div>
    <div class="pane" id="pane-timeline">
      ${timelineHtml || `<div class="tl-title" style="color:var(--z-text-secondary)">No timeline events.</div>`}
    </div>
    <div class="pane" id="pane-code">
      ${renderDiffHtml(data.diff)}
    </div>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    const openBtn = document.getElementById('openExternal');
    if (openBtn) openBtn.onclick = () => vscode.postMessage({ type: 'openExternal' });
    for (const tab of document.querySelectorAll('#tabs .tab')) {
      tab.addEventListener('click', () => {
        for (const t of document.querySelectorAll('#tabs .tab')) t.classList.remove('active');
        for (const p of document.querySelectorAll('.pane')) p.classList.remove('active');
        tab.classList.add('active');
        document.getElementById('pane-' + tab.dataset.tab).classList.add('active');
      });
    }
  </script>
</body>
</html>`;
  }
}
