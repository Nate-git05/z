/**
 * Sidebar webview providers — Profile is live in Phase 3; others stay placeholders.
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { AuthStatus } from "./appServerClient";

export function registerViews(
  context: vscode.ExtensionContext,
  manager: AppServerManager
): { refreshProfile: () => void } {
  const profile = new ProfileViewProvider(manager);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("z.profile", profile)
  );

  for (const viewId of [
    "z.chat",
    "z.uncertainty",
    "z.skills",
    "z.commitBlocks",
    "z.mcp",
  ]) {
    context.subscriptions.push(
      vscode.window.registerWebviewViewProvider(
        viewId,
        new PlaceholderViewProvider(viewId, manager)
      )
    );
  }

  return {
    refreshProfile: () => profile.refresh(),
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
  <h3>Z · Profile</h3>
  <p class="muted">Account + app-server connection (Phase 3).</p>

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
  ${
    auth?.login?.status && auth.login.status !== "idle"
      ? `<div class="row"><div class="label">Login</div><div class="value">${escapeHtml(
          auth.login.status
        )}${
          auth.login.error ? ` — ${escapeHtml(auth.login.error)}` : ""
        }</div></div>`
      : ""
  }

  <div style="margin-top:14px">
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

class PlaceholderViewProvider implements vscode.WebviewViewProvider {
  constructor(
    private readonly viewId: string,
    private readonly manager: AppServerManager
  ) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    const title = this.viewId.replace(/^z\./, "");
    const conn = this.manager.connectionState;
    webviewView.webview.options = { enableScripts: false };
    webviewView.webview.html = `<!DOCTYPE html>
<html><body style="font-family: var(--vscode-font-family); padding: 12px; color: var(--vscode-foreground);">
  <h3 style="margin:0 0 8px">Z · ${escapeHtml(title)}</h3>
  <p style="opacity:0.8;margin:0 0 8px">Panel lands in a later phase. App-server: <strong>${escapeHtml(
    conn
  )}</strong>.</p>
  <p style="opacity:0.65;margin:0;font-size:12px">Folder open / tabs / save come from the VS Code shell.</p>
</body></html>`;
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
