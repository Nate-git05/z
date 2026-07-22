/**
 * Phase 6 — Uncertainty chain view: sort, expand ResolutionContract, live subscribe.
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { zThemeCss } from "./zTheme";

interface ResolutionContract {
  node_id?: string;
  acceptable_evidence?: string[];
  contradiction_signals?: string[];
  expires_after_task?: boolean;
  source_requirement_id?: string | null;
}

interface UncNode {
  id?: string;
  title?: string;
  type?: string;
  risk_tier?: string;
  status?: string;
  summary?: string;
  explanation?: string;
  why_uncertain?: string;
  what_could_go_wrong?: string;
  suggested_fix?: string;
  task_title?: string | null;
  task_id?: string | null;
  area?: string;
  files_affected?: string[];
  created_at?: string;
  resolution_contract?: ResolutionContract | null;
  expires_after_task?: boolean;
}

type SortKey = "risk" | "age" | "type" | "status";

export class UncertaintyTreeProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private nodes: UncNode[] = [];
  private error: string | null = null;
  private sort: SortKey = "risk";
  private includeResolved = false;
  private subscribed = false;
  private expanded = new Set<string>();
  private busy = false;

  constructor(private readonly manager: AppServerManager) {
    manager.onNotification((method, params) => {
      if (method === "uncertainty/upsert") {
        const node = (params as { node?: UncNode })?.node;
        if (node?.id) {
          this.applyUpsert(node);
        } else {
          void this.refresh();
        }
        return;
      }
      if (
        method === "uncertainty/changed" ||
        method === "turn/started" ||
        method === "turn/completed" ||
        method === "turn/error" ||
        method === "gate/commit_blocked"
      ) {
        void this.refresh();
      }
      if (method === "turn/busy") {
        const state = String((params as { state?: string })?.state || "");
        this.busy = state === "busy" || state === "waiting_input";
        this.post();
      }
      if (method === "turn/completed" || method === "turn/error") {
        this.busy = false;
        this.post();
      }
    });
    manager.onDidChange(() => {
      void this.ensureSubscribed();
      void this.refresh();
    });
  }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = this.shellHtml();
    webviewView.webview.onDidReceiveMessage((msg) => void this.onMessage(msg));
    webviewView.onDidDispose(() => {
      this.view = undefined;
      void this.unsubscribe();
    });
    void this.ensureSubscribed();
    void this.refresh();
  }

  private async onMessage(msg: {
    type?: string;
    sort?: string;
    id?: string;
    includeResolved?: boolean;
  }): Promise<void> {
    if (!msg?.type) {
      return;
    }
    if (msg.type === "refresh") {
      await this.refresh();
      return;
    }
    if (msg.type === "sort" && msg.sort) {
      const s = msg.sort as SortKey;
      if (["risk", "age", "type", "status"].includes(s)) {
        this.sort = s;
        await this.refresh();
      }
      return;
    }
    if (msg.type === "toggleResolved") {
      this.includeResolved = Boolean(msg.includeResolved);
      await this.refresh();
      return;
    }
    if (msg.type === "toggle" && msg.id) {
      if (this.expanded.has(msg.id)) {
        this.expanded.delete(msg.id);
      } else {
        this.expanded.add(msg.id);
      }
      this.post();
      return;
    }
    if (msg.type === "subscribe") {
      await this.ensureSubscribed();
      this.post();
    }
  }

  private applyUpsert(node: UncNode): void {
    const id = node.id!;
    const idx = this.nodes.findIndex((n) => n.id === id);
    if (idx >= 0) {
      this.nodes[idx] = node;
    } else {
      this.nodes.unshift(node);
    }
    this.error = null;
    this.post();
  }

  private async ensureSubscribed(): Promise<void> {
    if (!this.manager.rpc || this.subscribed) {
      return;
    }
    try {
      const result = (await this.manager.rpc.request("uncertainty/subscribe", {})) as {
        subscribed?: boolean;
      };
      this.subscribed = Boolean(result.subscribed);
    } catch {
      this.subscribed = false;
    }
  }

  private async unsubscribe(): Promise<void> {
    if (!this.manager.rpc || !this.subscribed) {
      return;
    }
    try {
      await this.manager.rpc.request("uncertainty/unsubscribe", {});
    } catch {
      /* ignore */
    }
    this.subscribed = false;
  }

  async refresh(): Promise<void> {
    if (!this.manager.rpc) {
      this.nodes = [];
      this.error = null;
      this.post();
      return;
    }
    try {
      await this.ensureSubscribed();
      const result = (await this.manager.rpc.request("uncertainty/list", {
        sort: this.sort,
        includeResolved: this.includeResolved,
      })) as { nodes?: UncNode[]; subscribed?: boolean };
      this.nodes = Array.isArray(result.nodes) ? result.nodes : [];
      if (typeof result.subscribed === "boolean") {
        this.subscribed = result.subscribed;
      }
      this.error = null;
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.post();
  }

  private post(): void {
    if (!this.view) {
      return;
    }
    const openCount = this.nodes.filter((n) => n.status !== "resolved").length;
    this.view.badge = openCount
      ? { value: openCount, tooltip: `${openCount} open uncertainty` }
      : undefined;
    this.view.webview.postMessage({
      type: "state",
      connection: this.manager.connectionState,
      nodes: this.nodes,
      error: this.error,
      sort: this.sort,
      includeResolved: this.includeResolved,
      subscribed: this.subscribed,
      expanded: Array.from(this.expanded),
      live: this.busy || this.subscribed,
    });
  }

  private shellHtml(): string {
    return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  ${zThemeCss()}
  html, body {
    height: 100%; margin: 0; padding: 0;
    font-size: 12.5px;
  }
  #hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 14px 6px; gap: 8px;
  }
  h3 { margin: 0; font-size: 13px; font-weight: 600; color: var(--z-accent-bright); }
  .live {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--z-muted);
  }
  .live.on { color: var(--z-accent); }
  #toolbar {
    display: flex; flex-wrap: wrap; gap: 4px; padding: 4px 12px 8px; align-items: center;
  }
  #toolbar select, #toolbar label {
    font-size: 11px; padding: 2px 4px;
  }
  #toolbar label { display: flex; align-items: center; gap: 4px; border: none; background: transparent; color: var(--z-muted); }
  #chain { padding: 0 14px 18px; overflow-y: auto; }
  .group-title {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--z-text-secondary); padding: 10px 4px 6px;
  }
  .card {
    position: relative; margin: 0 0 2px 10px; padding: 10px 12px 10px 16px;
    border-left: 2px solid var(--z-border);
    cursor: pointer;
  }
  .card::before {
    content: ''; position: absolute; left: -5px; top: 14px;
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--z-accent-dim);
  }
  .card.High { border-left-color: var(--z-accent-bright); }
  .card.High::before { background: var(--z-accent-bright); }
  .card.Medium { border-left-color: var(--z-accent); }
  .card.Medium::before { background: var(--z-accent); }
  .card .title { font-weight: 600; line-height: 1.3; color: var(--z-text); }
  .card .meta { color: var(--z-muted); margin-top: 2px; font-size: 11px; }
  .card .sum { color: var(--z-muted); margin-top: 4px; line-height: 1.35; }
  .detail {
    margin-top: 8px; padding-top: 8px;
    border-top: 1px solid var(--z-border);
    font-size: 11px; line-height: 1.4; color: var(--z-muted);
  }
  .detail .label {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--z-accent); margin: 6px 0 2px;
  }
  .detail ul { margin: 0; padding-left: 16px; }
  .empty, .err { padding: 12px; color: var(--z-muted); line-height: 1.4; }
  .err { color: var(--z-accent-bright); }
  button.secondary, #refresh {
    background: transparent; color: var(--z-text);
    border: 1px solid var(--z-border);
    padding: 2px 8px; font-size: 11px; font-weight: 500;
  }
</style>
</head>
<body>
  <div id="hdr">
    <h3>Uncertainty</h3>
    <div style="display:flex;gap:8px;align-items:center">
      <span class="live" id="live">idle</span>
      <button id="refresh">↻</button>
    </div>
  </div>
  <div id="toolbar">
    <select id="sort">
      <option value="risk">Sort: risk</option>
      <option value="age">Sort: age</option>
      <option value="type">Sort: type</option>
      <option value="status">Sort: status</option>
    </select>
    <label><input type="checkbox" id="resolved" /> Resolved</label>
  </div>
  <div id="chain"><div class="empty">No open uncertainty yet.</div></div>
  <script>
    const vscode = acquireVsCodeApi();
    const chain = document.getElementById('chain');
    const live = document.getElementById('live');
    const sortEl = document.getElementById('sort');
    const resolvedEl = document.getElementById('resolved');
    document.getElementById('refresh').onclick = () => vscode.postMessage({ type: 'refresh' });
    sortEl.onchange = () => vscode.postMessage({ type: 'sort', sort: sortEl.value });
    resolvedEl.onchange = () => vscode.postMessage({ type: 'toggleResolved', includeResolved: resolvedEl.checked });

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function render(nodes, expanded) {
      const open = new Set(expanded || []);
      if (!nodes.length) {
        chain.innerHTML = '<div class="empty">No open uncertainty yet. Points of low confidence appear here as the agent works.</div>';
        return;
      }
      const groups = new Map();
      for (const n of nodes) {
        const key = n.task_title || n.task_id || 'General';
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(n);
      }
      let html = '';
      for (const [name, items] of groups) {
        html += '<div class="group-title">' + escapeHtml(name) + '</div>';
        for (const n of items) {
          const risk = n.risk_tier || 'Low';
          const id = n.id || '';
          const files = (n.files_affected || []).slice(0, 3).join(', ');
          const isOpen = open.has(id);
          const rc = n.resolution_contract || {};
          html += '<div class="card ' + escapeHtml(risk) + '" data-id="' + escapeHtml(id) + '">'
            + '<div class="title">' + escapeHtml(n.title || 'Untitled') + (isOpen ? ' ▾' : ' ▸') + '</div>'
            + '<div class="meta">' + escapeHtml(risk) + ' · ' + escapeHtml(n.type || '')
            + (n.status ? ' · ' + escapeHtml(n.status) : '')
            + (n.expires_after_task || rc.expires_after_task ? ' · temp' : '')
            + (files ? ' · ' + escapeHtml(files) : '')
            + '</div>'
            + (n.summary ? '<div class="sum">' + escapeHtml(n.summary) + '</div>' : '');
          if (isOpen) {
            html += '<div class="detail">';
            if (n.why_uncertain) html += '<div class="label">Why uncertain</div><div>' + escapeHtml(n.why_uncertain) + '</div>';
            if (n.what_could_go_wrong) html += '<div class="label">What could go wrong</div><div>' + escapeHtml(n.what_could_go_wrong) + '</div>';
            if (n.suggested_fix) html += '<div class="label">Suggested fix</div><div>' + escapeHtml(n.suggested_fix) + '</div>';
            html += '<div class="label">Resolution contract</div>';
            const ev = rc.acceptable_evidence || [];
            const contra = rc.contradiction_signals || [];
            if (ev.length || contra.length) {
              if (ev.length) html += '<div>Evidence: <ul>' + ev.map(e => '<li>' + escapeHtml(e) + '</li>').join('') + '</ul></div>';
              if (contra.length) html += '<div>Contradictions: <ul>' + contra.map(e => '<li>' + escapeHtml(e) + '</li>').join('') + '</ul></div>';
              html += '<div>Expires after task: ' + (rc.expires_after_task ? 'yes' : 'no') + '</div>';
            } else {
              html += '<div style="opacity:0.7">No contract attached yet.</div>';
            }
            html += '</div>';
          }
          html += '</div>';
        }
      }
      chain.innerHTML = html;
      for (const el of chain.querySelectorAll('.card[data-id]')) {
        el.addEventListener('click', () => vscode.postMessage({ type: 'toggle', id: el.dataset.id }));
      }
    }

    window.addEventListener('message', (e) => {
      const d = e.data || {};
      if (d.type !== 'state') return;
      if (d.sort) sortEl.value = d.sort;
      resolvedEl.checked = !!d.includeResolved;
      if (d.error) {
        chain.innerHTML = '<div class="err">' + escapeHtml(d.error) + '</div>';
      } else {
        render(d.nodes || [], d.expanded || []);
      }
      const label = d.subscribed ? (d.live ? 'live' : 'subscribed') : (d.connection || 'offline');
      live.textContent = label;
      live.className = 'live' + ((d.subscribed || d.live) ? ' on' : '');
    });
  </script>
</body>
</html>`;
  }
}
