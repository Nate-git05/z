/**
 * Agent-first layout:
 * - Center: Main Chat panel (editor area)
 * - Left: Uncertainty Tree + Skills + MCP + Profile
 * - Right: Commit Gate
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { AuthStatus } from "./appServerClient";
import { MainChatPanel } from "./chatPanel";
import { UncertaintyTreeProvider } from "./uncertaintyView";
import { CommitGateProvider } from "./commitGateView";
import { SkillsViewProvider } from "./skillsView";
import { McpViewProvider } from "./mcpView";
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

interface UsageSummary {
  range?: string;
  source?: string;
  note?: string;
  error?: string | null;
  authenticated?: boolean;
  byModel?: UsageRow[];
  total_requests?: number;
  totalRequests?: number;
  total_cost_usd?: number;
  totalCostUsd?: number;
}

export function registerViews(
  context: vscode.ExtensionContext,
  manager: AppServerManager
): {
  refreshProfile: () => void;
  refreshChat: () => void;
  openChat: () => void;
} {
  const chat = new MainChatPanel(context, manager);
  const uncertainty = new UncertaintyTreeProvider(manager);
  const skills = new SkillsViewProvider(manager);
  const mcp = new McpViewProvider(manager);
  const commitGate = new CommitGateProvider(manager);
  const profile = new ProfileViewProvider(manager);

  context.subscriptions.push(
    { dispose: () => chat.dispose() },
    vscode.window.registerWebviewViewProvider("z.uncertainty", uncertainty, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.window.registerWebviewViewProvider("z.skills", skills, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.window.registerWebviewViewProvider("z.mcp", mcp, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.window.registerWebviewViewProvider("z.commitGate", commitGate, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.window.registerWebviewViewProvider("z.profile", profile)
  );

  // z.openChat is registered in extension.ts first (portable VS Code harden).
  context.subscriptions.push(
    vscode.commands.registerCommand("z.focusUncertainty", async () => {
      await vscode.commands.executeCommand("workbench.view.extension.z-left");
      await vscode.commands.executeCommand("z.uncertainty.focus");
    }),
    vscode.commands.registerCommand("z.focusSkills", async () => {
      await vscode.commands.executeCommand("workbench.view.extension.z-left");
      try {
        await vscode.commands.executeCommand("z.skills.focus");
      } catch {
        /* ignore */
      }
    }),
    vscode.commands.registerCommand("z.focusMcp", async () => {
      await vscode.commands.executeCommand("workbench.view.extension.z-left");
      try {
        await vscode.commands.executeCommand("z.mcp.focus");
      } catch {
        /* ignore */
      }
    }),
    vscode.commands.registerCommand("z.focusCommitGate", async () => {
      await vscode.commands.executeCommand("workbench.view.extension.z-left");
      try {
        await vscode.commands.executeCommand("z.commitGate.focus");
      } catch {
        /* ignore */
      }
    })
  );

  return {
    refreshProfile: () => profile.refresh(),
    refreshChat: () => chat.refresh(),
    openChat: () => chat.show(),
  };
}

class ProfileViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private range: "billing_period" | "all" = "billing_period";

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
    let authErr: string | null = null;
    let usage: UsageSummary | null = null;
    let usageErr: string | null = null;

    if (this.manager.rpc) {
      try {
        auth = await this.manager.authStatus();
      } catch (err) {
        authErr = err instanceof Error ? err.message : String(err);
      }
      try {
        usage = (await this.manager.rpc.request("usage/summary", {
          range: this.range,
        })) as UsageSummary;
      } catch (err) {
        usageErr = err instanceof Error ? err.message : String(err);
      }
    }

    const info = this.manager.serverInfo;
    const byModel = usage?.byModel || [];
    const totalRequests = usage?.totalRequests ?? usage?.total_requests ?? 0;
    const totalCost = usage?.totalCostUsd ?? usage?.total_cost_usd ?? 0;
    const usageAuthed = usage?.authenticated !== false && Boolean(auth?.authenticated);
    const maxCost = Math.max(1e-9, ...byModel.map((r) => Number(r.costUsd ?? r.cost_usd ?? 0)));
    const maxReq = Math.max(1, ...byModel.map((r) => Number(r.requests ?? 0)));

    const barsHtml = !usageAuthed
      ? `<p class="muted">Sign in to see live gateway usage.</p>`
      : usage?.error
        ? `<p class="bad">${escapeHtml(String(usage.error))}</p>`
        : byModel.length
      ? byModel
          .map((row) => {
            const model = row.modelId || row.model_id || "unknown";
            const req = Number(row.requests || 0);
            const cost = Number(row.costUsd ?? row.cost_usd ?? 0);
            const inTok = Number(row.inputTokens ?? row.input_tokens ?? 0);
            const outTok = Number(row.outputTokens ?? row.output_tokens ?? 0);
            const wCost = Math.max(4, Math.round((cost / maxCost) * 100));
            const wReq = Math.max(4, Math.round((req / maxReq) * 100));
            return `<div class="bar-row">
  <div class="bar-label">${escapeHtml(model)}</div>
  <div class="bar-track"><div class="bar-fill" style="width:${wCost}%"></div></div>
  <div class="bar-meta">$${cost.toFixed(2)} · ${req} req · ${formatTok(inTok)}/${formatTok(outTok)} tok</div>
  <div class="bar-track thin"><div class="bar-fill dim" style="width:${wReq}%"></div></div>
</div>`;
          })
          .join("")
      : `<p class="muted">No gateway requests this period.</p>`;

    const tableRows = usageAuthed && byModel.length
      ? byModel
          .map((row) => {
            const model = row.modelId || row.model_id || "unknown";
            const req = Number(row.requests || 0);
            const cost = Number(row.costUsd ?? row.cost_usd ?? 0);
            const inTok = Number(row.inputTokens ?? row.input_tokens ?? 0);
            const outTok = Number(row.outputTokens ?? row.output_tokens ?? 0);
            return `<tr>
  <td>${escapeHtml(model)}</td>
  <td class="num">${req}</td>
  <td class="num">${formatTok(inTok)}</td>
  <td class="num">${formatTok(outTok)}</td>
  <td class="num">$${cost.toFixed(2)}</td>
</tr>`;
          })
          .join("")
      : "";

    webview.html = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  ${zThemeCss()}
  body {
    font-family: "IBM Plex Mono", "JetBrains Mono", ui-monospace, monospace;
    padding: 12px;
    margin: 0;
    font-size: 13px;
  }
  h3 { margin: 0 0 10px; font-weight: 600; color: var(--z-accent-bright); }
  h4 { margin: 16px 0 8px; font-weight: 600; color: var(--z-accent); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
  .muted { color: var(--z-muted); margin: 0 0 12px; line-height: 1.4; }
  .row { margin: 8px 0; }
  .label { color: var(--z-accent); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  .value { margin-top: 2px; word-break: break-all; }
  button { margin: 4px 6px 4px 0; }
  button.active { outline: 1px solid var(--z-accent); }
  .ok { color: var(--z-accent); }
  .bad { color: var(--z-accent-bright); }
  .totals {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin: 10px 0 14px;
  }
  .total {
    border-top: 1px solid rgba(201,106,43,0.3);
    padding-top: 6px;
  }
  .total .n { font-size: 18px; color: var(--z-text); font-weight: 600; }
  .bar-row { margin: 10px 0; }
  .bar-label { margin-bottom: 4px; color: var(--z-text); }
  .bar-track {
    height: 8px;
    background: #1a1a1a;
    border: 1px solid rgba(201,106,43,0.2);
  }
  .bar-track.thin { height: 4px; margin-top: 4px; }
  .bar-fill { height: 100%; background: var(--z-accent); }
  .bar-fill.dim { background: rgba(224,120,48,0.45); }
  .bar-meta { font-size: 11px; color: var(--z-muted); margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 8px; }
  th, td { text-align: left; padding: 4px 2px; border-bottom: 1px solid rgba(201,106,43,0.15); }
  th { color: var(--z-accent); font-weight: 600; }
  td.num, th.num { text-align: right; }
</style>
</head>
<body>
  <h3>Profile</h3>
  <p class="muted">Account · usage · app-server. Chat is the center panel.</p>

  <div class="row">
    <div class="label">App server</div>
    <div class="value ${conn === "connected" ? "ok" : "bad"}">${escapeHtml(conn)}${
      this.manager.errorMessage
        ? ` — ${escapeHtml(this.manager.errorMessage)}`
        : ""
    }</div>
  </div>
  ${
    info
      ? `<div class="row"><div class="label">Server</div><div class="value">${escapeHtml(
          info.serverInfo.name
        )} ${escapeHtml(info.serverInfo.version)}</div></div>
         <div class="row"><div class="label">Workspace</div><div class="value">${escapeHtml(
           info.workspaceRoot || "—"
         )}</div></div>`
      : ""
  }

  <div class="row">
    <div class="label">Account</div>
    <div class="value">${
      auth?.authenticated
        ? escapeHtml(auth.displayName || auth.email || "signed in")
        : authErr
          ? escapeHtml(authErr)
          : "Not signed in"
    }</div>
  </div>
  ${
    auth?.authenticated
      ? `<div class="row"><div class="label">Mode / model</div><div class="value">${escapeHtml(
          auth.auth_mode || "—"
        )} · ${escapeHtml(auth.selected_model || "—")}</div></div>`
      : ""
  }

  <h4>Usage</h4>
  <div>
    <button class="${this.range === "billing_period" ? "active" : "secondary"}" data-cmd="setRange" data-range="billing_period">Billing period</button>
    <button class="${this.range === "all" ? "active" : "secondary"}" data-cmd="setRange" data-range="all">All time</button>
  </div>
  ${
    usageErr
      ? `<p class="bad">${escapeHtml(usageErr)}</p>`
      : usage
        ? `${
            usageAuthed
              ? `<div class="totals">
  <div class="total"><div class="label">Requests</div><div class="n">${totalRequests}</div></div>
  <div class="total"><div class="label">Cost (USD)</div><div class="n">$${Number(totalCost).toFixed(2)}</div></div>
</div>`
              : ""
          }
${usage.note ? `<p class="muted">${escapeHtml(String(usage.note))}</p>` : ""}
${barsHtml}
${
  tableRows
    ? `<table>
  <thead><tr><th>Model</th><th class="num">Req</th><th class="num">In</th><th class="num">Out</th><th class="num">Cost</th></tr></thead>
  <tbody>${tableRows}</tbody>
</table>`
    : ""
}`
        : `<p class="muted">Connect app-server to load usage.</p>`
  }

  <div style="margin-top:14px">
    <button data-cmd="openChat">Open Chat</button>
    ${
      auth?.authenticated
        ? `<button class="secondary" data-cmd="signOut">Sign out</button>`
        : `<button data-cmd="signIn">Sign in</button>`
    }
    <button class="secondary" data-cmd="reconnect">Reconnect</button>
    <button class="secondary" data-cmd="refresh">Refresh</button>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    for (const btn of document.querySelectorAll('button[data-cmd]')) {
      btn.addEventListener('click', () => {
        const type = btn.dataset.cmd;
        if (type === 'setRange') {
          vscode.postMessage({ type: 'setRange', range: btn.dataset.range });
        } else {
          vscode.postMessage({ type });
        }
      });
    }
  </script>
</body>
</html>`;
  }
}

function formatTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
