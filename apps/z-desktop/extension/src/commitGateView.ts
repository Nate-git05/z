/**
 * Right sidebar — Commit Gate (ready vs blocked).
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { zThemeCss } from "./zTheme";

interface BlockRecord {
  id?: string;
  reason?: string;
  state?: string;
  verify_state?: string | null;
  created_at?: string;
  updated_at?: string;
  extra?: { dirty_count?: number };
}

export class CommitGateProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private blocks: BlockRecord[] = [];
  private error: string | null = null;

  constructor(private readonly manager: AppServerManager) {
    manager.onNotification((method) => {
      if (
        method === "gate/commit_blocked" ||
        method === "turn/completed" ||
        method === "turn/error" ||
        method === "uncertainty/changed" ||
        method === "turn/started"
      ) {
        void this.refresh();
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
    });
    void this.refresh();
  }

  async refresh(): Promise<void> {
    if (!this.manager.rpc) {
      this.blocks = [];
      this.error = null;
      this.post();
      return;
    }
    try {
      const result = (await this.manager.rpc.request("commit_blocks/list", {})) as {
        blocks?: BlockRecord[];
      };
      this.blocks = Array.isArray(result.blocks) ? result.blocks : [];
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
    const blocked = this.blocks.filter((b) => (b.state || "blocked") === "blocked");
    const cleared = this.blocks.filter((b) => b.state === "resolved" || b.state === "overridden");
    this.view.webview.postMessage({
      type: "state",
      connection: this.manager.connectionState,
      blocked,
      cleared,
      error: this.error,
      canCommit: blocked.length === 0,
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
    font-family: "IBM Plex Mono", "JetBrains Mono", ui-monospace, monospace;
    font-size: 12px;
  }
  #hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 12px 6px;
  }
  h3 { margin: 0; font-size: 13px; font-weight: 600; color: var(--z-accent-bright); }
  #banner {
    margin: 0 12px 10px; padding: 8px 10px; font-size: 12px; font-weight: 600;
    border: 1px solid var(--z-border); background: var(--z-raised);
  }
  #banner.ok { color: var(--z-muted); border-color: var(--z-border); }
  #banner.blocked { color: var(--z-accent-bright); border-color: var(--z-accent); }
  section { padding: 0 12px 14px; }
  .sec-title {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--z-accent); margin-bottom: 6px;
  }
  .item {
    padding: 8px 0; border-bottom: 1px solid var(--z-border);
  }
  .item .reason { line-height: 1.35; }
  .item .meta { color: var(--z-muted); margin-top: 3px; font-size: 11px; }
  .empty { color: var(--z-muted); line-height: 1.4; }
  .err { color: var(--z-accent-bright); padding: 12px; }
  button.secondary, #refresh {
    background: transparent; color: var(--z-text);
    border: 1px solid var(--z-border);
    padding: 2px 8px; font-size: 11px; font-weight: 500;
  }
</style>
</head>
<body>
  <div id="hdr">
    <h3>Commit Gate</h3>
    <button id="refresh">↻</button>
  </div>
  <div id="banner" class="ok">Checking…</div>
  <div id="body"></div>
  <script>
    const vscode = acquireVsCodeApi();
    document.getElementById('refresh').onclick = () => vscode.postMessage({ type: 'refresh' });

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function fmtTime(iso) {
      if (!iso) return '';
      try { return new Date(iso).toLocaleString(); } catch { return iso; }
    }

    function list(items, emptyText) {
      if (!items.length) return '<div class="empty">' + escapeHtml(emptyText) + '</div>';
      return items.map(b => {
        const dirty = b.extra && b.extra.dirty_count != null ? (' · dirty ' + b.extra.dirty_count) : '';
        return '<div class="item"><div class="reason">' + escapeHtml(b.reason || 'Blocked') + '</div>'
          + '<div class="meta">' + escapeHtml(b.state || 'blocked')
          + (b.verify_state ? ' · ' + escapeHtml(b.verify_state) : '')
          + dirty
          + (b.created_at ? ' · ' + escapeHtml(fmtTime(b.created_at)) : '')
          + '</div></div>';
      }).join('');
    }

    window.addEventListener('message', (e) => {
      const d = e.data || {};
      if (d.type !== 'state') return;
      const banner = document.getElementById('banner');
      const body = document.getElementById('body');
      if (d.error) {
        banner.className = 'blocked';
        banner.textContent = 'Gate unavailable';
        body.innerHTML = '<div class="err">' + escapeHtml(d.error) + '</div>';
        return;
      }
      const blocked = d.blocked || [];
      const cleared = d.cleared || [];
      if (d.canCommit) {
        banner.className = 'ok';
        banner.textContent = 'Ready — no active blockers';
      } else {
        banner.className = 'blocked';
        banner.textContent = blocked.length + ' blocking commit';
      }
      body.innerHTML =
        '<section><div class="sec-title">Blocked</div>' + list(blocked, 'Nothing blocked.') + '</section>'
        + '<section><div class="sec-title">Cleared / overridden</div>' + list(cleared, 'None yet.') + '</section>';
    });
  </script>
</body>
</html>`;
  }
}
