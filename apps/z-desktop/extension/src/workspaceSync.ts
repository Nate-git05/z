/**
 * Phase 3a — sync VS Code folder open with z-app-server workspace/open.
 * Tabs / save / Monaco remain inherited from Code - OSS.
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";

export function registerWorkspaceSync(
  context: vscode.ExtensionContext,
  manager: AppServerManager
): void {
  const sync = async (folders: readonly vscode.WorkspaceFolder[] | undefined) => {
    const root = folders?.[0]?.uri.fsPath;
    if (!root || !manager.rpc) {
      return;
    }
    try {
      await manager.openWorkspace(root);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      vscode.window.showWarningMessage(`Z workspace sync failed: ${msg}`);
    }
  };

  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(async (e) => {
      if (e.added.length || e.removed.length) {
        await sync(vscode.workspace.workspaceFolders);
      }
    })
  );

  // Initial sync after connect is handled by initialize(workspaceRoot); this
  // covers later folder switches.
  context.subscriptions.push(
    manager.onDidChange(async () => {
      if (manager.connectionState === "connected") {
        await sync(vscode.workspace.workspaceFolders);
      }
    })
  );
}
