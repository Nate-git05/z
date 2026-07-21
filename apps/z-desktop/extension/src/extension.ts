import * as vscode from "vscode";
import { spawn, ChildProcess } from "child_process";
import { AppServerClient } from "./appServerClient";

let client: AppServerClient | null = null;
let appServerProc: ChildProcess | null = null;
let status: vscode.StatusBarItem;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.text = "$(debug-disconnect) Z";
  status.tooltip = "Z app-server: disconnected";
  status.command = "z.showStatus";
  status.show();
  context.subscriptions.push(status);

  context.subscriptions.push(
    vscode.commands.registerCommand("z.startAppServer", () => startAppServer()),
    vscode.commands.registerCommand("z.reconnectAppServer", () => reconnect()),
    vscode.commands.registerCommand("z.showStatus", () => showStatus())
  );

  // Placeholder webview providers for each Z sidebar view (Phase 0 scaffold).
  for (const viewId of [
    "z.chat",
    "z.uncertainty",
    "z.skills",
    "z.commitBlocks",
    "z.profile",
    "z.mcp",
  ]) {
    context.subscriptions.push(
      vscode.window.registerWebviewViewProvider(
        viewId,
        new PlaceholderViewProvider(viewId)
      )
    );
  }

  const cfg = vscode.workspace.getConfiguration("z");
  if (cfg.get<boolean>("autoStartAppServer", true)) {
    await startAppServer().catch(() => {
      /* best-effort on activate */
    });
    await reconnect().catch(() => {
      /* status bar shows disconnected */
    });
  }
}

export function deactivate(): void {
  client?.disconnect();
  client = null;
  if (appServerProc && !appServerProc.killed) {
    appServerProc.kill();
    appServerProc = null;
  }
}

function appServerUrl(): string {
  return (
    process.env.Z_APP_SERVER_URL ||
    vscode.workspace.getConfiguration("z").get<string>("appServerUrl") ||
    "ws://127.0.0.1:8741"
  );
}

async function startAppServer(): Promise<void> {
  if (appServerProc && !appServerProc.killed) {
    vscode.window.showInformationMessage("Z app-server already running.");
    return;
  }
  try {
    appServerProc = spawn("z", ["app-server"], {
      stdio: "ignore",
      detached: false,
      env: { ...process.env },
    });
    appServerProc.on("exit", () => {
      appServerProc = null;
    });
  } catch (err) {
    vscode.window.showErrorMessage(
      `Could not spawn z app-server: ${err instanceof Error ? err.message : err}`
    );
  }
}

async function reconnect(): Promise<void> {
  client?.disconnect();
  client = new AppServerClient(appServerUrl());
  try {
    await client.connect();
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const init = await client.initialize(root);
    status.text = "$(check) Z";
    status.tooltip = `Z app-server ${init.serverInfo.version} · ${init.capabilities.join(", ")}`;
  } catch (err) {
    status.text = "$(debug-disconnect) Z";
    status.tooltip = `Z app-server offline: ${err instanceof Error ? err.message : err}`;
    throw err;
  }
}

async function showStatus(): Promise<void> {
  if (!client?.connected) {
    const pick = await vscode.window.showWarningMessage(
      "Z app-server is not connected.",
      "Start",
      "Reconnect"
    );
    if (pick === "Start") {
      await startAppServer();
      await reconnect();
    } else if (pick === "Reconnect") {
      await reconnect();
    }
    return;
  }
  try {
    const auth = (await client.request("auth/status")) as {
      authenticated?: boolean;
      email?: string;
      auth_mode?: string;
      selected_model?: string;
    };
    vscode.window.showInformationMessage(
      `Z connected · auth=${auth.authenticated ? auth.email || "yes" : "no"} · mode=${
        auth.auth_mode || "—"
      } · model=${auth.selected_model || "—"}`
    );
  } catch (err) {
    vscode.window.showErrorMessage(
      `auth/status failed: ${err instanceof Error ? err.message : err}`
    );
  }
}

class PlaceholderViewProvider implements vscode.WebviewViewProvider {
  constructor(private readonly viewId: string) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    const title = this.viewId.replace(/^z\./, "");
    webviewView.webview.options = { enableScripts: false };
    webviewView.webview.html = `<!DOCTYPE html>
<html><body style="font-family: var(--vscode-font-family); padding: 12px; color: var(--vscode-foreground);">
  <h3 style="margin:0 0 8px">Z · ${escapeHtml(title)}</h3>
  <p style="opacity:0.8;margin:0">Phase 0 scaffold. Wire live data via z-app-server in later phases.</p>
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
