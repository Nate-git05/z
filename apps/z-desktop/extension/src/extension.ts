import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { registerAuthCommands } from "./authCommands";
import { registerWorkspaceSync } from "./workspaceSync";
import { registerViews } from "./views";

let manager: AppServerManager | null = null;
let status: vscode.StatusBarItem;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  manager = new AppServerManager(context);
  context.subscriptions.push(manager);

  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = "z.showStatus";
  status.show();
  context.subscriptions.push(status);

  const { refreshProfile } = registerViews(context, manager);
  const refreshUi = () => {
    updateStatusBar(manager!);
    refreshProfile();
  };
  context.subscriptions.push(manager.onDidChange(refreshUi));

  registerAuthCommands(context, manager, refreshUi);
  registerWorkspaceSync(context, manager);

  context.subscriptions.push(
    vscode.commands.registerCommand("z.startAppServer", async () => {
      try {
        await manager!.startProcess();
        await manager!.ensureConnected();
        vscode.window.showInformationMessage("Z app-server ready.");
        refreshUi();
      } catch (err) {
        vscode.window.showErrorMessage(
          `Start failed: ${err instanceof Error ? err.message : err}`
        );
        refreshUi();
      }
    }),
    vscode.commands.registerCommand("z.stopAppServer", async () => {
      await manager!.stop();
      vscode.window.showInformationMessage("Z app-server stopped.");
      refreshUi();
    }),
    vscode.commands.registerCommand("z.reconnectAppServer", async () => {
      try {
        await manager!.ensureConnected();
        refreshUi();
      } catch (err) {
        vscode.window.showErrorMessage(
          `Reconnect failed: ${err instanceof Error ? err.message : err}`
        );
        refreshUi();
      }
    }),
    vscode.commands.registerCommand("z.showStatus", () => showStatus(manager!, refreshUi)),
    vscode.commands.registerCommand("z.openAppServerLog", () => {
      // Output channel is created inside manager; expose via command palette indirectly
      vscode.commands.executeCommand("workbench.action.output.show");
    })
  );

  updateStatusBar(manager);

  const cfg = vscode.workspace.getConfiguration("z");
  if (cfg.get<boolean>("autoStartAppServer", true)) {
    try {
      await manager.ensureConnected();
      refreshUi();
    } catch {
      refreshUi();
    }
  }
}

export function deactivate(): void {
  manager?.dispose();
  manager = null;
}

function updateStatusBar(m: AppServerManager): void {
  const state = m.connectionState;
  if (state === "connected") {
    status.text = "$(check) Z";
    const ver = m.serverInfo?.serverInfo.version || "";
    status.tooltip = `Z app-server connected${ver ? ` · ${ver}` : ""}`;
    status.backgroundColor = undefined;
  } else if (state === "starting" || state === "connecting") {
    status.text = "$(sync~spin) Z";
    status.tooltip = `Z app-server ${state}…`;
    status.backgroundColor = undefined;
  } else if (state === "error") {
    status.text = "$(error) Z";
    status.tooltip = m.errorMessage || "Z app-server error";
    status.backgroundColor = new vscode.ThemeColor("statusBarItem.errorBackground");
  } else {
    status.text = "$(debug-disconnect) Z";
    status.tooltip = "Z app-server: disconnected";
    status.backgroundColor = undefined;
  }
}

async function showStatus(m: AppServerManager, refreshUi: () => void): Promise<void> {
  if (!m.rpc) {
    const pick = await vscode.window.showWarningMessage(
      "Z app-server is not connected.",
      "Start",
      "Reconnect"
    );
    if (pick === "Start") {
      await vscode.commands.executeCommand("z.startAppServer");
    } else if (pick === "Reconnect") {
      await vscode.commands.executeCommand("z.reconnectAppServer");
    }
    return;
  }
  try {
    const auth = await m.authStatus();
    const pick = await vscode.window.showInformationMessage(
      `Z connected · ${
        auth.authenticated ? auth.email || auth.displayName || "signed in" : "not signed in"
      } · mode=${auth.auth_mode || "—"} · model=${auth.selected_model || "—"}`,
      auth.authenticated ? "Sign out" : "Sign in",
      "Refresh"
    );
    if (pick === "Sign in") {
      await vscode.commands.executeCommand("z.signIn");
    } else if (pick === "Sign out") {
      await vscode.commands.executeCommand("z.signOut");
    }
    refreshUi();
  } catch (err) {
    vscode.window.showErrorMessage(
      `Status failed: ${err instanceof Error ? err.message : err}`
    );
  }
}
