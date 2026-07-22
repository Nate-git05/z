/**
 * Phase 8 — Commit Gate: blocked vs ready, with explicit override confirm.
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
  thread_id?: string | null;
  session_id?: string | null;
  override_meta?: { reason?: string; note?: string; by?: string } | null;
  extra?: { dirty_count?: number };
}

export class CommitGateProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private blocks: BlockRecord[] = [];
  private error: string | null = null;
  private status: string | null = null;
  /** Two-step override: first click arms, second confirms. */
  private armedOverrideId: string | null = null;

  constructor(private readonly manager: AppServerManager) {
    manager.onNotification((method) => {
      if (
        method === "gate/commit_blocked" ||
        method === "gate/commit_updated" ||
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
    webviewView.webview.onDidReceiveMessage((msg) => void this.onMessage(msg));
    webviewView.onDidDispose(() => {
      this.view = undefined;
    });
    void this.refresh();
  }

  private async onMessage(msg: {
    type?: string;
    id?: string;
    reason?: string;
  }): Promise<void> {
    if (!msg?.type) {
      return;
    }
    if (msg.type === "refresh") {
      this.armedOverrideId = null;
      await this.refresh();
      return;
    }
    if (msg.type === "armOverride" && msg.id) {
      this.armedOverrideId = String(msg.id);
      this.status = "Click Confirm override to proceed — this skips the gate.";
      this.post();
      return;
    }
    if (msg.type === "cancelArm") {
      this.armedOverrideId = null;
      this.status = null;
      this.post();
      return;
    }
    if (msg.type === "confirmOverride" && msg.id) {
      if (this.armedOverrideId !== msg.id) {
        this.status = "Arm the override first (two-step confirm).";
        this.post();
        return;
      }
      await this.overrideBlock(String(msg.id), String(msg.reason || ""));
      return;
    }
    if (msg.type === "resolve" && msg.id) {
      await this.resolveBlock(String(msg.id));
      return;
    }
    if (msg.type === "openChat") {
      await vscode.commands.executeCommand("z.openChat");
    }
  }

  private async overrideBlock(id: string, reason: string): Promise<void> {
    if (!this.manager.rpc) {
      return;
    }
    try {
      await this.manager.rpc.request("commit_blocks/override", {
        id,
        confirm: true,
        reason: reason || "user override from Commit Gate",
      });
      this.armedOverrideId = null;
      this.status = "Block overridden.";
      await this.refresh();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
    }
  }

  private async resolveBlock(id: string): Promise<void> {
    if (!this.manager.rpc) {
      return;
    }
    try {
      await this.manager.rpc.request("commit_blocks/resolve", {
        id,
        note: "marked resolved from Commit Gate",
      });
      this.armedOverrideId = null;
      this.status = "Block marked resolved.";
      await this.refresh();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
    }
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
    const cleared = this.blocks.filter(
      (b) => b.state === "resolved" || b.state === "overridden"
    );
    this.view.webview.postMessage({
      type: "state",
      connection: this.manager.connectionState,
      blocked,
      cleared,
      error: this.error,
      status: this.status,
      canCommit: blocked.length === 0,
      armedOverrideId: this.armedOverrideId,
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
    font-family: var(--z-font-ui);
    font-size: 13px;
  }
  #hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 14px 8px;
  }
  h3 { margin: 0; font-size: 14px; font-weight: 600; color: var(--z-text); }
  #banner {
    margin: 0 12px 10px; padding: 8px 12px; font-size: 12px; font-weight: 500;
    border: 1px solid var(--z-border); background: var(--z-surface);
    border-radius: var(--z-radius-sm);
    display: flex; align-items: center; gap: 8px;
  }
  #banner .dot {
    width: 7px; height: 7px; border-radius: 50%; background: var(--z-status-ok);
  }
  #banner.ok { color: var(--z-secondary); }
  #banner.blocked { color: var(--z-text); border-color: rgba(217,119,87,0.45); }
  #banner.blocked .dot { background: var(--z-status-blocked); }
  #msg { padding: 0 14px 8px; font-size: 11px; color: var(--z-accent); min-height: 14px; }
  #msg.err { color: var(--z-status-blocked); }
  section { padding: 0 8px 14px; }
  .sec-title {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--z-muted); margin: 0 6px 6px;
  }
  .item .actions { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 12px 10px 40px; }
  .item.armed {
    border-radius: var(--z-radius-sm);
    background: var(--z-accent-wash);
    border-left: 2px solid var(--z-accent);
  }
  .warn {
    font-size: 11px; color: var(--z-accent); margin: 0 12px 6px 40px; line-height: 1.35;
  }
  .empty { color: var(--z-muted); line-height: 1.4; padding: 8px 12px; }
  button { font-size: 11px; padding: 4px 10px; }
  button.secondary, #refresh {
    background: transparent; color: var(--z-text);
    border: 1px solid var(--z-border); font-weight: 500;
  }
  button.danger {
    background: var(--z-status-blocked); color: var(--z-bg);
  }
</style>
</head>
<body>
  <div id="hdr">
    <h3>Commit Gate</h3>
    <div style="display:flex;gap:6px">
      <button class="secondary" id="chat">Chat</button>
      <button class="secondary" id="refresh">↻</button>
    </div>
  </div>
  <div id="banner" class="ok">Checking…</div>
  <div id="msg"></div>
  <div id="body"></div>
  <script>
    const vscode = acquireVsCodeApi();
    document.getElementById('refresh').onclick = () => vscode.postMessage({ type: 'refresh' });
    document.getElementById('chat').onclick = () => vscode.postMessage({ type: 'openChat' });

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function fmtTime(iso) {
      if (!iso) return '';
      try { return new Date(iso).toLocaleString(); } catch { return iso; }
    }

    function blockedList(items, armedId) {
      if (!items.length) return '<div class="empty">Nothing blocked — gate is clear.</div>';
      return items.map(b => {
        const id = b.id || '';
        const armed = armedId && armedId === id;
        const dirty = b.extra && b.extra.dirty_count != null ? (' · dirty ' + b.extra.dirty_count) : '';
        let actions = '';
        if (armed) {
          actions =
            '<div class="warn">This will allow commit despite the block. Confirm?</div>'
            + '<div class="actions">'
            + '<button class="danger" data-act="confirmOverride" data-id="' + escapeHtml(id) + '">Confirm override</button>'
            + '<button class="secondary" data-act="cancelArm">Cancel</button>'
            + '</div>';
        } else {
          actions =
            '<div class="actions">'
            + '<button class="secondary" data-act="armOverride" data-id="' + escapeHtml(id) + '">Override…</button>'
            + '<button class="secondary" data-act="resolve" data-id="' + escapeHtml(id) + '">Mark resolved</button>'
            + '</div>';
        }
        return '<div class="item' + (armed ? ' armed' : '') + '">'
          + '<div class="list-row">'
          + '<div class="glyph">◉<span class="dot"></span></div>'
          + '<div class="title">' + escapeHtml(b.reason || 'Blocked') + '</div>'
          + '<div class="time">' + escapeHtml(fmtTime(b.created_at)) + '</div>'
          + '<div class="meta">' + escapeHtml(b.state || 'blocked')
          + (b.verify_state ? ' · ' + escapeHtml(b.verify_state) : '')
          + dirty
          + (b.thread_id ? ' · thread ' + escapeHtml(b.thread_id) : '')
          + '</div>'
          + '</div>'
          + actions
          + '</div>';
      }).join('');
    }

    function clearedList(items) {
      if (!items.length) return '<div class="empty">None yet.</div>';
      return items.map(b => {
        const meta = b.override_meta || {};
        return '<div class="item"><div class="list-row">'
          + '<div class="glyph">○<span class="dot ok"></span></div>'
          + '<div class="title">' + escapeHtml(b.reason || '') + '</div>'
          + '<div class="time">' + escapeHtml(fmtTime(b.updated_at)) + '</div>'
          + '<div class="meta">' + escapeHtml(b.state || '')
          + (meta.reason ? ' · ' + escapeHtml(meta.reason) : '')
          + (meta.note ? ' · ' + escapeHtml(meta.note) : '')
          + '</div></div></div>';
      }).join('');
    }

    window.addEventListener('message', (e) => {
      const d = e.data || {};
      if (d.type !== 'state') return;
      const banner = document.getElementById('banner');
      const body = document.getElementById('body');
      const msg = document.getElementById('msg');
      msg.textContent = d.error || d.status || '';
      msg.className = d.error ? 'err' : '';
      if (d.error) {
        banner.className = 'blocked';
        banner.textContent = 'Gate unavailable';
        body.innerHTML = '<div class="empty">' + escapeHtml(d.error) + '</div>';
        return;
      }
      const blocked = d.blocked || [];
      const cleared = d.cleared || [];
      if (d.canCommit) {
        banner.className = 'ok';
        banner.innerHTML = '<span class="dot"></span>Ready — no active blockers';
      } else {
        banner.className = 'blocked';
        banner.innerHTML = '<span class="dot"></span>' + blocked.length + ' blocking commit';
      }
      body.innerHTML =
        '<section><div class="sec-title">Blocked</div>' + blockedList(blocked, d.armedOverrideId) + '</section>'
        + '<section><div class="sec-title">Cleared / overridden</div>' + clearedList(cleared) + '</section>';
      for (const btn of body.querySelectorAll('button[data-act]')) {
        btn.addEventListener('click', () => {
          const act = btn.getAttribute('data-act');
          const id = btn.getAttribute('data-id') || undefined;
          vscode.postMessage({ type: act, id });
        });
      }
    });
  </script>
</body>
</html>`;
  }
}
