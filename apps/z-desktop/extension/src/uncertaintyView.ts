/**
 * Left sidebar — live Uncertainty Tree (risk-ranked hierarchy).
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";

interface UncNode {
  id?: string;
  title?: string;
  type?: string;
  risk_tier?: string;
  status?: string;
  summary?: string;
  task_title?: string | null;
  task_id?: string | null;
  area?: string;
  files_affected?: string[];
}

export class UncertaintyTreeProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private nodes: UncNode[] = [];
  private error: string | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private busy = false;

  constructor(private readonly manager: AppServerManager) {
    manager.onNotification((method) => {
      if (
        method === "uncertainty/changed" ||
        method === "uncertainty/upsert" ||
        method === "turn/started" ||
        method === "turn/completed" ||
        method === "turn/busy" ||
        method === "turn/error" ||
        method === "gate/commit_blocked"
      ) {
        if (method === "turn/busy") {
          /* busy handled below via params in a separate path */
        }
        void this.refresh();
      }
    });
    manager.onNotification((method, params) => {
      if (method === "turn/busy") {
        const state = String((params as { state?: string })?.state || "");
        this.busy = state === "busy" || state === "waiting_input";
        this.ensurePoll();
      }
      if (method === "turn/completed" || method === "turn/error") {
        this.busy = false;
        this.ensurePoll();
      }
    });
    manager.onDidChange(() => void this.refresh());
  }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = this.shellHtml();
    webviewView.webview.onDidReceiveMessage((msg) => {
      if (msg?.type === "refresh") {
        void this.refresh();
      }
    });
    webviewView.onDidDispose(() => {
      this.view = undefined;
      this.stopPoll();
    });
    void this.refresh();
    this.ensurePoll();
  }

  private ensurePoll(): void {
    if (this.busy && !this.pollTimer) {
      this.pollTimer = setInterval(() => void this.refresh(), 2500);
    } else if (!this.busy) {
      this.stopPoll();
    }
  }

  private stopPoll(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  async refresh(): Promise<void> {
    if (!this.manager.rpc) {
      this.nodes = [];
      this.error = null;
      this.post();
      return;
    }
    try {
      const result = (await this.manager.rpc.request("uncertainty/list", {
        sort: "risk",
      })) as { nodes?: UncNode[] };
      this.nodes = Array.isArray(result.nodes) ? result.nodes : [];
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
    this.view.webview.postMessage({
      type: "state",
      connection: this.manager.connectionState,
      nodes: this.nodes,
      error: this.error,
      live: this.busy,
    });
  }

  private shellHtml(): string {
    return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  :root { color-scheme: light dark; }
  html, body {
    height: 100%; margin: 0; padding: 0;
    font-family: var(--vscode-font-family);
    color: var(--vscode-foreground);
    background: var(--vscode-sideBar-background);
    font-size: 12px;
  }
  #hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 12px 6px; gap: 8px;
  }
  h3 { margin: 0; font-size: 13px; font-weight: 600; }
  .live {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em;
    opacity: 0.5;
  }
  .live.on { opacity: 1; color: var(--vscode-charts-orange, #e2a03e); }
  #tree { padding: 4px 8px 16px; overflow-y: auto; }
  .group { margin: 0 0 12px; }
  .group-title {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em;
    opacity: 0.55; padding: 4px 6px; margin-bottom: 2px;
  }
  .node {
    padding: 7px 8px; margin: 0 0 2px; border-left: 2px solid transparent;
    cursor: default;
  }
  .node.High { border-left-color: var(--vscode-errorForeground, #f14c4c); }
  .node.Medium { border-left-color: var(--vscode-charts-orange, #e2a03e); }
  .node.Low { border-left-color: var(--vscode-descriptionForeground, #888); }
  .node .title { font-weight: 600; line-height: 1.3; }
  .node .meta { opacity: 0.65; margin-top: 2px; font-size: 11px; }
  .node .sum { opacity: 0.8; margin-top: 3px; line-height: 1.35; }
  .empty, .err { padding: 12px; opacity: 0.7; line-height: 1.4; }
  .err { color: var(--vscode-errorForeground); }
  button {
    background: transparent; color: var(--vscode-foreground);
    border: 1px solid var(--vscode-panel-border, rgba(127,127,127,0.4));
    padding: 2px 8px; font-size: 11px; cursor: pointer;
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
  <div id="tree"><div class="empty">No open uncertainty yet.</div></div>
  <script>
    const vscode = acquireVsCodeApi();
    const tree = document.getElementById('tree');
    const live = document.getElementById('live');
    document.getElementById('refresh').onclick = () => vscode.postMessage({ type: 'refresh' });

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function render(nodes) {
      if (!nodes.length) {
        tree.innerHTML = '<div class="empty">No open uncertainty yet. Points of low confidence appear here as the agent works.</div>';
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
        html += '<div class="group"><div class="group-title">' + escapeHtml(name) + '</div>';
        for (const n of items) {
          const risk = n.risk_tier || 'Low';
          const files = (n.files_affected || []).slice(0, 3).join(', ');
          html += '<div class="node ' + escapeHtml(risk) + '">'
            + '<div class="title">' + escapeHtml(n.title || 'Untitled') + '</div>'
            + '<div class="meta">' + escapeHtml(risk) + ' · ' + escapeHtml(n.type || '')
            + (n.status ? ' · ' + escapeHtml(n.status) : '')
            + (files ? ' · ' + escapeHtml(files) : '')
            + '</div>'
            + (n.summary ? '<div class="sum">' + escapeHtml(n.summary) + '</div>' : '')
            + '</div>';
        }
        html += '</div>';
      }
      tree.innerHTML = html;
    }

    window.addEventListener('message', (e) => {
      const d = e.data || {};
      if (d.type !== 'state') return;
      if (d.error) {
        tree.innerHTML = '<div class="err">' + escapeHtml(d.error) + '</div>';
      } else {
        render(d.nodes || []);
      }
      live.textContent = d.live ? 'live' : (d.connection === 'connected' ? 'synced' : d.connection || 'offline');
      live.className = 'live' + (d.live ? ' on' : '');
    });
  </script>
</body>
</html>`;
  }
}
