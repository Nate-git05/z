/**
 * Agent-first layout:
 * - Center: Main Chat panel (editor area)
 * - Left: Uncertainty Tree + Skills + Profile
 * - Right: Commit Gate
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { AuthStatus } from "./appServerClient";
import { MainChatPanel } from "./chatPanel";
import { UncertaintyTreeProvider } from "./uncertaintyView";
import { CommitGateProvider } from "./commitGateView";
import { SkillsViewProvider } from "./skillsView";

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
    vscode.window.registerWebviewViewProvider("z.commitGate", commitGate, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.window.registerWebviewViewProvider("z.profile", profile)
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("z.openChat", () => chat.show()),
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
    vscode.commands.registerCommand("z.focusCommitGate", async () => {
      try {
        await vscode.commands.executeCommand("workbench.action.focusAuxiliaryBar");
      } catch {
        /* older shells */
      }
      await vscode.commands.executeCommand("workbench.view.extension.z-right");
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
    if (this.manager.rpc) {
      try {
        auth = await this.manager.authStatus();
      } catch (err) {
        authErr = err instanceof Error ? err.message : String(err);
      }
    }

    const info = this.manager.serverInfo;
    webview.html = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  body {
    font-family: var(--vscode-font-family);
    color: var(--vscode-foreground);
    padding: 12px;
    margin: 0;
    font-size: 13px;
  }
  h3 { margin: 0 0 10px; font-weight: 600; }
  .muted { opacity: 0.75; margin: 0 0 12px; line-height: 1.4; }
  .row { margin: 8px 0; }
  .label { opacity: 0.7; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  .value { margin-top: 2px; word-break: break-all; }
  button {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none;
    padding: 6px 12px;
    margin: 4px 6px 4px 0;
    cursor: pointer;
  }
  button.secondary {
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
  }
  .ok { color: var(--vscode-testing-iconPassed, #3fb950); }
  .bad { color: var(--vscode-errorForeground); }
</style>
</head>
<body>
  <h3>Profile</h3>
  <p class="muted">Account · app-server. Chat is the center panel.</p>

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
      btn.addEventListener('click', () => vscode.postMessage({ type: btn.dataset.cmd }));
    }
  </script>
</body>
</html>`;
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
