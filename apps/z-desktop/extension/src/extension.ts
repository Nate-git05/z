import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { registerAuthCommands } from "./authCommands";
import { registerWorkspaceSync } from "./workspaceSync";
import { registerViews } from "./views";
import { ensureEngineOrWizard } from "./firstRun";

let manager: AppServerManager | null = null;
let status: vscode.StatusBarItem | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  // Absolute first: register Open Chat so palette never says "not found"
  // even if the rest of activate throws on old/portable VS Code builds.
  let openChatImpl: (() => void) | null = null;
  context.subscriptions.push(
    vscode.commands.registerCommand("z.openChat", () => {
      if (openChatImpl) {
        openChatImpl();
        return;
      }
      void vscode.window.showErrorMessage(
        "Z Chat is still starting — reload the window (Developer: Reload Window) and try again."
      );
    })
  );

  try {
    manager = new AppServerManager(context);
    context.subscriptions.push(manager);

    status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    status.command = "z.showStatus";
    status.show();
    context.subscriptions.push(status);

    const { refreshProfile, openChat } = registerViews(context, manager);
    openChatImpl = () => openChat();

    const refreshUi = () => {
      if (manager) {
        updateStatusBar(manager);
      }
      refreshProfile();
    };
    context.subscriptions.push(manager.onDidChange(refreshUi));

    try {
      registerAuthCommands(context, manager, refreshUi);
    } catch (err) {
      console.error("Z auth commands failed", err);
    }
    try {
      registerWorkspaceSync(context, manager);
    } catch (err) {
      console.error("Z workspace sync failed", err);
    }

    context.subscriptions.push(
      vscode.commands.registerCommand("z.applyTerminalTheme", () => applyZTerminalTheme(true)),
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
        vscode.commands.executeCommand("workbench.action.output.show");
      }),
      vscode.commands.registerCommand("z.installEngineHelp", () =>
        ensureEngineOrWizard(context)
      )
    );

    updateStatusBar(manager);

    const cfg = vscode.workspace.getConfiguration("z");
    try {
      if (cfg.get<boolean>("applyTerminalThemeOnActivate", true)) {
        await applyZTerminalTheme();
      }
    } catch {
      /* ignore */
    }
    try {
      if (cfg.get<boolean>("openChatOnActivate", true)) {
        openChat();
        void vscode.commands.executeCommand("workbench.view.extension.z-left");
      }
    } catch {
      /* ignore */
    }
    try {
      await ensureEngineOrWizard(context);
    } catch {
      /* ignore */
    }
    if (cfg.get<boolean>("autoStartAppServer", true)) {
      try {
        await manager.ensureConnected();
        refreshUi();
      } catch {
        refreshUi();
      }
    }
  } catch (err) {
    console.error("Z extension activate failed", err);
    void vscode.window.showErrorMessage(
      `Z extension failed to activate: ${err instanceof Error ? err.message : err}. Check Output → Extension Host.`
    );
  }
}

export function deactivate(): void {
  manager?.dispose();
  manager = null;
}

/** Apply burnt-orange / near-black theme matching aider/z/theme.py (CLI). */
async function applyZTerminalTheme(showToast = false): Promise<void> {
  const themeId = "z.z-editor-Z Terminal";
  try {
    await vscode.workspace
      .getConfiguration("workbench")
      .update("colorTheme", themeId, vscode.ConfigurationTarget.Global);
    if (showToast) {
      vscode.window.showInformationMessage("Z Terminal theme applied (orange / black).");
    }
  } catch (err) {
    if (showToast) {
      vscode.window.showWarningMessage(
        `Could not apply Z Terminal theme: ${err instanceof Error ? err.message : err}`
      );
    }
  }
}

function updateStatusBar(m: AppServerManager): void {
  if (!status) {
    return;
  }
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
