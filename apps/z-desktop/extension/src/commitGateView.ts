/**
 * Phase 8 — Commit Gate: blocked vs ready, with explicit override confirm.
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { zThemeCss } from "./zTheme";
import { listRowCss } from "./listRow";
import { DetailPanelManager, DetailData } from "./detailPanel";

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

interface GitCommitSummary {
  sha: string;
  shortSha: string;
  summary: string;
  message: string;
  author: string;
  authoredAt: string;
  insertions: number;
  deletions: number;
  filesChanged: number;
}

interface GitHubPrSummary {
  number: number;
  title: string;
  state: string;
  draft: boolean;
  author: string | null;
  mine: boolean;
  reviewRequested: boolean;
  branch: string | null;
  baseBranch: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  htmlUrl: string | null;
}

export class CommitGateProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private blocks: BlockRecord[] = [];
  private pushed: GitCommitSummary[] = [];
  private prConnected = false;
  private prOwner: string | null = null;
  private prRepo: string | null = null;
  private prs: GitHubPrSummary[] = [];
  private error: string | null = null;
  private status: string | null = null;
  /** Two-step override: first click arms, second confirms. */
  private armedOverrideId: string | null = null;

  constructor(
    private readonly manager: AppServerManager,
    private readonly detailPanel: DetailPanelManager
  ) {
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
      return;
    }
    if (msg.type === "connectGithub") {
      await vscode.commands.executeCommand("z.focusMcp");
      return;
    }
    if (msg.type === "openCommit" && msg.id) {
      await this.openCommitDetail(String(msg.id));
      return;
    }
    if (msg.type === "openPr" && msg.id) {
      await this.openPrDetail(Number(msg.id));
    }
  }

  private async openCommitDetail(sha: string): Promise<void> {
    if (!this.manager.rpc) {
      return;
    }
    try {
      const commit = (await this.manager.rpc.request("git/show", { sha })) as GitCommitSummary & {
        diff: string;
      };
      this.detailPanel.open({
        id: `commit:${commit.sha}`,
        kind: "commit",
        title: commit.summary,
        subtitle: `${commit.author} · ${fmtTimeServer(commit.authoredAt)} · ${commit.shortSha}`,
        summaryRows: [
          { label: "Author", value: commit.author },
          { label: "SHA", value: commit.sha },
          {
            label: "Stats",
            value: `+${commit.insertions} -${commit.deletions} · ${commit.filesChanged} file(s)`,
          },
        ],
        description: commit.message,
        timeline: [
          { title: "Committed", time: fmtTimeServer(commit.authoredAt), status: "ok" },
        ],
        diff: commit.diff,
      });
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
    }
  }

  private async openPrDetail(number: number): Promise<void> {
    if (!this.manager.rpc) {
      return;
    }
    try {
      const result = (await this.manager.rpc.request("github/prs/get", { number })) as {
        pr: {
          number: number;
          title: string;
          body: string;
          state: string;
          draft: boolean;
          author: string | null;
          branch: string | null;
          baseBranch: string | null;
          reviewers: string[];
          additions: number;
          deletions: number;
          changedFiles: number;
          createdAt: string | null;
          htmlUrl: string | null;
        };
        checks: Array<{ name: string; conclusion?: string | null }>;
        comments: Array<{ author: string | null; body: string; createdAt: string | null }>;
        diff: string;
      };
      const { pr, checks, comments, diff } = result;
      const timeline: DetailData["timeline"] = [
        { title: `${pr.author || "someone"} opened this pull request`, time: fmtTimeServer(pr.createdAt), status: "neutral" },
        ...comments.map((c) => ({
          title: `${c.author || "someone"} commented`,
          detail: c.body,
          time: fmtTimeServer(c.createdAt),
          status: "neutral" as const,
        })),
      ];
      this.detailPanel.open({
        id: `pr:${this.prOwner}/${this.prRepo}#${pr.number}`,
        kind: "pr",
        title: pr.title,
        subtitle: `${pr.author || "unknown"} · #${pr.number} · ${pr.draft ? "Draft" : pr.state}`,
        summaryRows: [
          { label: "Branch", value: `${pr.branch || "?"} → ${pr.baseBranch || "?"}` },
          { label: "Reviewers", value: pr.reviewers.length ? pr.reviewers.join(", ") : "No reviewers" },
          { label: "Comments", value: String(comments.length) },
          {
            label: "Checks",
            value: checks.length
              ? `${checks.filter((c) => c.conclusion === "failure").length} failing · ${checks.filter((c) => c.conclusion === "success").length} passed`
              : "No checks",
          },
          { label: "Changes", value: `+${pr.additions} -${pr.deletions} · ${pr.changedFiles} file(s)` },
        ],
        description: pr.body,
        checks,
        timeline,
        diff,
        externalUrl: pr.htmlUrl || undefined,
      });
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
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
      this.pushed = [];
      this.prConnected = false;
      this.prs = [];
      this.error = null;
      this.post();
      return;
    }
    const rpc = this.manager.rpc;
    try {
      const result = (await rpc.request("commit_blocks/list", {})) as { blocks?: BlockRecord[] };
      this.blocks = Array.isArray(result.blocks) ? result.blocks : [];
      this.error = null;
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    try {
      const result = (await rpc.request("git/log", {})) as { commits?: GitCommitSummary[] };
      this.pushed = Array.isArray(result.commits) ? result.commits : [];
    } catch {
      this.pushed = [];
    }
    try {
      const result = (await rpc.request("github/prs/list", {})) as {
        connected?: boolean;
        owner?: string | null;
        repo?: string | null;
        prs?: GitHubPrSummary[];
      };
      this.prConnected = Boolean(result.connected);
      this.prOwner = result.owner ?? null;
      this.prRepo = result.repo ?? null;
      this.prs = Array.isArray(result.prs) ? result.prs : [];
    } catch {
      this.prConnected = false;
      this.prs = [];
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
      pushed: this.pushed,
      prConnected: this.prConnected,
      prOwner: this.prOwner,
      prRepo: this.prRepo,
      prs: this.prs,
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
  ${listRowCss()}
  html, body {
    height: 100%; margin: 0; padding: 0;
    font-size: 12.5px;
  }
  #hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 14px 8px;
  }
  h3 { margin: 0; font-size: 13px; font-weight: 600; color: var(--z-accent-bright); }
  #banner {
    margin: 0 14px 10px; padding: 8px 10px; font-size: 12px; font-weight: 600;
    border: 1px solid var(--z-border); background: var(--z-raised); border-radius: var(--z-radius-sm);
  }
  #banner.ok { color: var(--z-text-secondary); border-color: var(--z-border); }
  #banner.blocked { color: var(--z-status-blocked); border-color: var(--z-status-blocked); }
  #msg { padding: 0 14px 8px; font-size: 11px; color: var(--z-status-ok); min-height: 14px; }
  #msg.err { color: var(--z-status-blocked); }
  #tabs { padding: 0 10px; }
  #body { padding: 0 10px 16px; }
  .row-actions { padding: 0 8px 10px 38px; }
  .row-actions .actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .row-actions.armed { background: var(--z-raised); border-radius: var(--z-radius-sm); padding-top: 8px; }
  .warn {
    font-size: 11px; color: var(--z-status-blocked); line-height: 1.4;
  }
  button { font-size: 11px; padding: 5px 12px; }
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
  <div id="tabs"></div>
  <div id="body"></div>
  <script>
    const vscode = acquireVsCodeApi();
    document.getElementById('refresh').onclick = () => vscode.postMessage({ type: 'refresh' });
    document.getElementById('chat').onclick = () => vscode.postMessage({ type: 'openChat' });

    let data = { blocked: [], cleared: [], pushed: [], prs: [], prConnected: false, canCommit: true, armedOverrideId: null };
    let activeTab = 'blocked';
    let openId = null;

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function fmtTime(iso) {
      if (!iso) return '';
      try { return new Date(iso).toLocaleString(); } catch { return iso; }
    }

    function buildTabs() {
      const tabs = [ ['blocked','Blocked'], ['cleared','Cleared'], ['pushed','Pushed'], ['prs','Pull Requests'] ];
      return '<div class="tabs">' + tabs.map(([id,label]) =>
        '<button class="tab' + (id === activeTab ? ' active' : '') + '" data-tab="' + id + '">' + label + '</button>'
      ).join('') + '</div>';
    }

    function dotHtml(status) {
      if (status === 'neutral') return '';
      return '<span class="dot ' + (status === 'ok' ? 'ok' : 'blocked') + '"></span>';
    }

    function diffStatHtml(add, del) {
      if (!add && !del) return '';
      return '<span class="diff">' + (add ? '<span class="add">+' + add + '</span>' : '') + (del ? '<span class="del">−' + del + '</span>' : '') + '</span>';
    }

    function buildRow(row) {
      return '<div class="list-row' + (row.dimmed ? ' dimmed' : '') + '" data-kind="' + row.kind + '" data-id="' + escapeHtml(row.id) + '">'
        + '<div class="glyph"><svg viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="3.2"/></svg>' + dotHtml(row.status) + '</div>'
        + '<div class="body">'
        + '<div class="top-line"><span class="title">' + escapeHtml(row.title) + '</span>'
        + (row.time ? '<span class="time">' + escapeHtml(row.time) + '</span>' : '')
        + diffStatHtml(row.diffAdd, row.diffDel) + '</div>'
        + (row.metaLeft ? '<div class="meta">' + escapeHtml(row.metaLeft) + '</div>' : '')
        + '</div></div>';
    }

    function buildBlockRow(b) {
      const id = b.id || '';
      const dirty = b.extra && b.extra.dirty_count != null ? ('dirty ' + b.extra.dirty_count) : '';
      const meta = [b.state || 'blocked'];
      if (b.verify_state) meta.push(b.verify_state);
      if (dirty) meta.push(dirty);
      if (b.thread_id) meta.push('thread ' + b.thread_id);
      if (b.created_at) meta.push(fmtTime(b.created_at));
      let html = buildRow({ id, kind: 'block', title: b.reason || 'Blocked', status: 'blocked', metaLeft: meta.join(' · ') });
      if (id === openId) html += buildActions(id);
      return html;
    }

    function buildPushedRow(c) {
      return buildRow({
        id: c.sha,
        kind: 'pushed',
        title: c.summary || '(no message)',
        status: 'neutral',
        metaLeft: c.shortSha + ' · ' + c.author,
        time: fmtTime(c.authoredAt),
        diffAdd: c.insertions,
        diffDel: c.deletions,
      });
    }

    function buildPrRow(pr) {
      const meta = ['#' + pr.number, pr.author || 'unknown'];
      if (pr.branch) meta.push(pr.branch + ' → ' + (pr.baseBranch || 'main'));
      return buildRow({
        id: String(pr.number),
        kind: 'pr',
        title: (pr.draft ? '[Draft] ' : '') + pr.title,
        status: 'neutral',
        metaLeft: meta.join(' · '),
        time: fmtTime(pr.updatedAt),
      });
    }

    function buildActions(id) {
      const armed = data.armedOverrideId && data.armedOverrideId === id;
      if (armed) {
        return '<div class="row-actions armed">'
          + '<div class="warn">This will allow commit despite the block. Confirm?</div>'
          + '<div class="actions"><button class="danger" data-act="confirmOverride" data-id="' + escapeHtml(id) + '">Confirm override</button>'
          + '<button class="secondary" data-act="cancelArm">Cancel</button></div></div>';
      }
      return '<div class="row-actions">'
        + '<div class="actions"><button class="secondary" data-act="armOverride" data-id="' + escapeHtml(id) + '">Override…</button>'
        + '<button class="secondary" data-act="resolve" data-id="' + escapeHtml(id) + '">Mark resolved</button></div></div>';
    }

    function buildClearedRow(b, idx) {
      const meta = b.override_meta || {};
      const parts = [b.state || ''];
      if (meta.reason) parts.push(meta.reason);
      if (meta.note) parts.push(meta.note);
      if (b.updated_at) parts.push(fmtTime(b.updated_at));
      return buildRow({
        id: b.id || ('cleared-' + idx),
        kind: 'cleared',
        title: b.reason || 'Cleared',
        status: 'ok',
        metaLeft: parts.filter(Boolean).join(' · '),
        dimmed: true,
      });
    }

    function render() {
      const banner = document.getElementById('banner');
      const msg = document.getElementById('msg');
      const tabsEl = document.getElementById('tabs');
      const body = document.getElementById('body');
      msg.textContent = data.error || data.status || '';
      msg.className = data.error ? 'err' : '';
      if (data.error) {
        banner.className = 'blocked';
        banner.textContent = 'Gate unavailable';
        tabsEl.innerHTML = '';
        body.innerHTML = '<div class="list-row-empty">' + escapeHtml(data.error) + '</div>';
        return;
      }
      const blocked = data.blocked || [];
      const cleared = data.cleared || [];
      const pushed = data.pushed || [];
      const prs = data.prs || [];
      banner.className = data.canCommit ? 'ok' : 'blocked';
      banner.textContent = data.canCommit ? 'Ready — no active blockers' : (blocked.length + ' blocking commit');
      tabsEl.innerHTML = buildTabs();
      let html = '';
      if (activeTab === 'blocked') {
        html = blocked.length
          ? blocked.map(buildBlockRow).join('')
          : '<div class="list-row-empty">Nothing blocked — gate is clear.</div>';
      } else if (activeTab === 'cleared') {
        html = cleared.length
          ? cleared.map(buildClearedRow).join('')
          : '<div class="list-row-empty">None yet.</div>';
      } else if (activeTab === 'pushed') {
        html = pushed.length
          ? pushed.map(buildPushedRow).join('')
          : '<div class="list-row-empty">No commits in this repo yet.</div>';
      } else if (activeTab === 'prs') {
        if (!data.prConnected) {
          html = '<div class="list-row-empty">'
            + (prs.length ? 'GitHub not connected.' : 'No GitHub remote detected, or GitHub is not connected.')
            + '<div class="actions" style="margin-top:10px"><button class="secondary" id="connectGithub">Connect GitHub</button></div></div>';
        } else {
          html = prs.length
            ? prs.map(buildPrRow).join('')
            : '<div class="list-row-empty">No pull requests found.</div>';
        }
      }
      body.innerHTML = html;
      const connectBtn = document.getElementById('connectGithub');
      if (connectBtn) connectBtn.addEventListener('click', () => vscode.postMessage({ type: 'connectGithub' }));
      for (const row of body.querySelectorAll('.list-row[data-id]')) {
        row.addEventListener('click', () => {
          const id = row.getAttribute('data-id');
          const kind = row.getAttribute('data-kind');
          if (kind === 'block') {
            openId = openId === id ? null : id;
            render();
          } else if (kind === 'pushed') {
            vscode.postMessage({ type: 'openCommit', id });
          } else if (kind === 'pr') {
            vscode.postMessage({ type: 'openPr', id });
          }
        });
      }
      for (const btn of body.querySelectorAll('button[data-act]')) {
        btn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const act = btn.getAttribute('data-act');
          const id = btn.getAttribute('data-id') || undefined;
          vscode.postMessage({ type: act, id });
        });
      }
      for (const t of tabsEl.querySelectorAll('.tab')) {
        t.addEventListener('click', () => {
          activeTab = t.getAttribute('data-tab');
          openId = null;
          render();
        });
      }
    }

    window.addEventListener('message', (e) => {
      const d = e.data || {};
      if (d.type !== 'state') return;
      data = d;
      render();
    });
  </script>
</body>
</html>`;
  }
}

function fmtTimeServer(iso: string | null | undefined): string {
  if (!iso) {
    return "";
  }
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
