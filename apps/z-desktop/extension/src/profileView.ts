/**
 * Codex-inspired Profile — avatar, stats strip, token heatmap with model hover.
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { AuthStatus } from "./appServerClient";
import { zThemeCss } from "./zTheme";

interface UsageRow {
  model_id?: string;
  modelId?: string;
  requests?: number;
  input_tokens?: number;
  inputTokens?: number;
  output_tokens?: number;
  outputTokens?: number;
  cost_usd?: number;
  costUsd?: number;
}

interface ActivityDay {
  date?: string;
  totalTokens?: number;
  models?: UsageRow[];
}

interface ActivityPayload {
  days?: ActivityDay[];
  totalTokens?: number;
  peakTokens?: number;
  authenticated?: boolean;
  note?: string | null;
}

export class ProfileViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private range: "billing_period" | "all" = "billing_period";
  private granularity: "daily" | "weekly" | "cumulative" = "daily";

  constructor(private readonly manager: AppServerManager) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.onDidReceiveMessage(async (msg) => {
      if (msg?.type === "signIn") {
        await vscode.commands.executeCommand("z.signIn");
        this.refresh();
      } else if (msg?.type === "signOut") {
        await vscode.commands.executeCommand("z.signOut");
        this.refresh();
      } else if (msg?.type === "reconnect") {
        await vscode.commands.executeCommand("z.reconnectAppServer");
        this.refresh();
      } else if (msg?.type === "refresh") {
        this.refresh();
      } else if (msg?.type === "openChat") {
        await vscode.commands.executeCommand("z.openChat");
      } else if (msg?.type === "setRange") {
        const next = String(msg.range || "");
        if (next === "billing_period" || next === "all") {
          this.range = next;
          this.refresh();
        }
      } else if (msg?.type === "setGranularity") {
        const g = String(msg.granularity || "");
        if (g === "daily" || g === "weekly" || g === "cumulative") {
          this.granularity = g;
          this.refresh();
        }
      }
    });
    void this.refresh();
  }

  refresh(): void {
    if (!this.view) {
      return;
    }
    void this.render(this.view.webview);
  }

  private async render(webview: vscode.Webview): Promise<void> {
    const conn = this.manager.connectionState;
    let auth: AuthStatus | null = null;
    let activity: ActivityPayload | null = null;
    let summaryNote: string | null = null;

    if (this.manager.rpc) {
      try {
        auth = await this.manager.authStatus();
      } catch {
        /* ignore */
      }
      try {
        activity = (await this.manager.rpc.request("usage/activity", {
          range: this.range,
          days: 371,
        })) as ActivityPayload;
      } catch (err) {
        summaryNote = err instanceof Error ? err.message : String(err);
      }
    }

    const name = auth?.displayName || auth?.email || "Z user";
    const handle = auth?.email ? `@${String(auth.email).split("@")[0]}` : "@guest";
    const initials = name
      .split(/\s+/)
      .map((p) => p[0] || "")
      .join("")
      .slice(0, 2)
      .toUpperCase() || "Z";
    const days = activity?.days || [];
    const totalTokens = Number(activity?.totalTokens || 0);
    const peakTokens = Number(activity?.peakTokens || totalTokens);
    const cells = days
      .map((d) => {
        const tok = Number(d.totalTokens || 0);
        const level =
          tok <= 0 ? 0 : tok < peakTokens * 0.25 ? 1 : tok < peakTokens * 0.5 ? 2 : tok < peakTokens * 0.75 ? 3 : 4;
        const tipModels = (d.models || [])
          .map((m) => {
            const id = shortModel(String(m.modelId || m.model_id || "model"));
            const inn = Number(m.inputTokens ?? m.input_tokens ?? 0);
            const out = Number(m.outputTokens ?? m.output_tokens ?? 0);
            const req = Number(m.requests || 0);
            const cost = Number(m.costUsd ?? m.cost_usd ?? 0);
            return `${id}: ${formatTok(inn)} in / ${formatTok(out)} out · ${req} req · $${cost.toFixed(2)}`;
          })
          .join("\n");
        const tip =
          tok > 0
            ? `${d.date}\n${formatTok(tok)} tokens\n${tipModels || "—"}`
            : `${d.date}\nNo activity`;
        return `<button type="button" class="cell l${level}" data-tip="${escapeAttr(tip)}" aria-label="${escapeAttr(tip)}"></button>`;
      })
      .join("");

    const authed = Boolean(auth?.authenticated);
    const modelLine = auth?.selected_model
      ? shortModel(String(auth.selected_model))
      : "—";

    webview.html = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  ${zThemeCss()}
  body { margin: 0; padding: 20px 18px 28px; font-size: 13px; line-height: 1.45; }
  .header { text-align: center; margin-bottom: 18px; }
  .avatar {
    width: 72px; height: 72px; border-radius: 50%; margin: 0 auto 12px;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(145deg, var(--z-accent-wash), rgba(247,165,107,0.35));
    color: var(--z-accent); font-size: 22px; font-weight: 700;
    border: 1px solid var(--z-border);
  }
  .name { font-size: 20px; font-weight: 600; color: var(--z-text); }
  .handle { color: var(--z-secondary); margin-top: 2px; }
  .badge {
    display: inline-block; margin-top: 8px; padding: 2px 10px;
    border-radius: 999px; font-size: 11px; color: var(--z-secondary);
    border: 1px solid var(--z-border); background: var(--z-surface);
  }
  .stats {
    display: flex; flex-wrap: wrap; gap: 0;
    border: 1px solid var(--z-border); border-radius: 999px;
    background: var(--z-surface); overflow: hidden; margin: 16px 0 20px;
  }
  .stat {
    flex: 1 1 88px; min-width: 88px; padding: 12px 10px; text-align: center;
    border-right: 1px solid var(--z-border);
  }
  .stat:last-child { border-right: none; }
  .stat .n { font-size: 16px; font-weight: 600; color: var(--z-text); font-variant-numeric: tabular-nums; }
  .stat .l { font-size: 10px; color: var(--z-muted); margin-top: 2px; }
  .section-head {
    display: flex; justify-content: space-between; align-items: center;
    margin: 8px 0 10px;
  }
  .section-head h3 {
    margin: 0; font-size: 13px; font-weight: 600; color: var(--z-text);
  }
  .toggles { display: flex; gap: 8px; }
  .toggles button {
    background: transparent; color: var(--z-secondary); border: none;
    padding: 4px 8px; font-weight: 500; font-size: 12px;
  }
  .toggles button.active { color: var(--z-text); background: var(--z-raised); border-radius: 8px; }
  .heat {
    display: flex; flex-wrap: wrap; gap: 3px;
    padding: 12px; border-radius: var(--z-radius);
    background: var(--z-surface); border: 1px solid var(--z-border);
  }
  .cell {
    width: 11px; height: 11px; border-radius: 3px; padding: 0; border: none;
    background: #1a1816; cursor: default;
  }
  .cell.l1 { background: rgba(247,165,107,0.22); }
  .cell.l2 { background: rgba(247,165,107,0.4); }
  .cell.l3 { background: rgba(247,165,107,0.62); }
  .cell.l4 { background: var(--z-accent); }
  .tip {
    display: none; position: fixed; z-index: 20; max-width: 280px;
    padding: 8px 10px; border-radius: 10px; font-size: 11px; white-space: pre-wrap;
    background: var(--z-raised); border: 1px solid var(--z-border); color: var(--z-text);
    pointer-events: none; box-shadow: 0 8px 24px rgba(0,0,0,0.35);
  }
  .tip.show { display: block; }
  .insights { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 18px; }
  .card {
    background: var(--z-surface); border: 1px solid var(--z-border);
    border-radius: var(--z-radius); padding: 14px;
  }
  .card h4 { margin: 0 0 10px; font-size: 12px; color: var(--z-secondary); font-weight: 600; }
  .kv { display: flex; justify-content: space-between; gap: 8px; margin: 6px 0; font-size: 12px; }
  .kv .k { color: var(--z-muted); } .kv .v { color: var(--z-text); text-align: right; }
  .actions { margin-top: 18px; display: flex; flex-wrap: wrap; gap: 8px; }
  .muted { color: var(--z-muted); font-size: 12px; }
  .conn { font-size: 11px; color: var(--z-secondary); margin-top: 6px; }
  .conn.ok { color: var(--z-status-ok); }
  @media (max-width: 420px) { .insights { grid-template-columns: 1fr; } }
</style>
</head>
<body>
  <div class="header">
    <div class="avatar">${escapeHtml(initials)}</div>
    <div class="name">${escapeHtml(name)}</div>
    <div class="handle">${escapeHtml(handle)}</div>
    <div class="badge">${authed ? "Signed in" : "Guest"}</div>
    <div class="conn ${conn === "connected" ? "ok" : ""}">App-server · ${escapeHtml(conn)}</div>
  </div>

  <div class="stats">
    <div class="stat"><div class="n">${formatTok(totalTokens)}</div><div class="l">Total tokens</div></div>
    <div class="stat"><div class="n">${formatTok(peakTokens)}</div><div class="l">Peak tokens</div></div>
    <div class="stat"><div class="n">—</div><div class="l">Longest chat</div></div>
    <div class="stat"><div class="n">—</div><div class="l">Current streak</div></div>
    <div class="stat"><div class="n">—</div><div class="l">Longest streak</div></div>
  </div>

  <div class="section-head">
    <h3>Token activity</h3>
    <div class="toggles">
      <button class="${this.granularity === "daily" ? "active" : ""}" data-g="daily">Daily</button>
      <button class="${this.granularity === "weekly" ? "active" : ""}" data-g="weekly">Weekly</button>
      <button class="${this.granularity === "cumulative" ? "active" : ""}" data-g="cumulative">Cumulative</button>
    </div>
  </div>
  ${summaryNote ? `<p class="muted">${escapeHtml(summaryNote)}</p>` : ""}
  ${activity?.note ? `<p class="muted">${escapeHtml(String(activity.note))}</p>` : ""}
  <div class="heat" id="heat">${cells || '<span class="muted">No activity yet</span>'}</div>
  <p class="muted" style="margin-top:8px">Hover a day to see model names and token usage.</p>

  <div class="insights">
    <div class="card">
      <h4>Activity insights</h4>
      <div class="kv"><span class="k">Active model</span><span class="v">${escapeHtml(modelLine)}</span></div>
      <div class="kv"><span class="k">Auth mode</span><span class="v">${escapeHtml(auth?.auth_mode || "—")}</span></div>
      <div class="kv"><span class="k">Range</span><span class="v">${escapeHtml(this.range)}</span></div>
    </div>
    <div class="card">
      <h4>Most used models</h4>
      ${
        (days.find((d) => (d.models || []).length)?.models || [])
          .slice(0, 4)
          .map((m) => {
            const id = shortModel(String(m.modelId || m.model_id || "model"));
            const tok =
              Number(m.inputTokens ?? m.input_tokens ?? 0) +
              Number(m.outputTokens ?? m.output_tokens ?? 0);
            return `<div class="kv"><span class="k">${escapeHtml(id)}</span><span class="v">${formatTok(tok)} tok</span></div>`;
          })
          .join("") || `<p class="muted">No model usage yet.</p>`
      }
    </div>
  </div>

  <div class="actions">
    <button data-cmd="openChat">Open Chat</button>
    ${
      authed
        ? `<button class="secondary" data-cmd="signOut">Sign out</button>`
        : `<button data-cmd="signIn">Sign in</button>`
    }
    <button class="secondary" data-cmd="reconnect">Reconnect</button>
    <button class="secondary" data-cmd="refresh">Refresh</button>
    <button class="secondary ${this.range === "billing_period" ? "" : ""}" data-cmd="setRange" data-range="billing_period">Billing</button>
    <button class="secondary" data-cmd="setRange" data-range="all">All time</button>
  </div>
  <div class="tip" id="tip"></div>
  <script>
    const vscode = acquireVsCodeApi();
    const tip = document.getElementById('tip');
    for (const btn of document.querySelectorAll('button[data-cmd]')) {
      btn.addEventListener('click', () => {
        const type = btn.dataset.cmd;
        if (type === 'setRange') vscode.postMessage({ type, range: btn.dataset.range });
        else vscode.postMessage({ type });
      });
    }
    for (const btn of document.querySelectorAll('.toggles button')) {
      btn.addEventListener('click', () => vscode.postMessage({ type: 'setGranularity', granularity: btn.dataset.g }));
    }
    for (const cell of document.querySelectorAll('.cell[data-tip]')) {
      cell.addEventListener('mouseenter', (e) => {
        tip.textContent = cell.getAttribute('data-tip') || '';
        tip.classList.add('show');
        const r = cell.getBoundingClientRect();
        tip.style.left = Math.min(window.innerWidth - 300, Math.max(8, r.left)) + 'px';
        tip.style.top = Math.max(8, r.top - 8 - tip.offsetHeight) + 'px';
      });
      cell.addEventListener('mouseleave', () => tip.classList.remove('show'));
    }
  </script>
</body>
</html>`;
  }
}

function shortModel(id: string): string {
  const slash = id.lastIndexOf("/");
  return slash >= 0 ? id.slice(slash + 1) : id;
}

function formatTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(Math.round(n));
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(s: string): string {
  return escapeHtml(s).replace(/`/g, "&#96;");
}
