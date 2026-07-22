/**
 * Agent-first layout registration.
 * Default (chatFirst): Chat center + overlay panels on demand.
 * Escape: z.uiShell=legacyStack restores stacked left webviews.
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { MainChatPanel } from "./chatPanel";
import { UncertaintyTreeProvider } from "./uncertaintyView";
import { CommitGateProvider } from "./commitGateView";
import { SkillsViewProvider } from "./skillsView";
import { McpViewProvider } from "./mcpView";
import { ProfileViewProvider } from "./profileView";
import { OverlayPanelHost } from "./overlayPanels";

export function registerViews(
  context: vscode.ExtensionContext,
  manager: AppServerManager
): {
  refreshProfile: () => void;
  refreshChat: () => void;
  openChat: () => void;
  openProfile: () => void;
  openGate: () => void;
} {
  const chat = new MainChatPanel(context, manager);
  const uncertainty = new UncertaintyTreeProvider(manager);
  const skills = new SkillsViewProvider(manager);
  const mcp = new McpViewProvider(manager);
  const commitGate = new CommitGateProvider(manager);
  const profile = new ProfileViewProvider(manager);
  const overlays = new OverlayPanelHost(context, manager, {
    uncertainty,
    skills,
    mcp,
    profile,
    commitGate,
  });

  const shell = () =>
    vscode.workspace.getConfiguration("z").get<string>("uiShell") || "chatFirst";

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

  const openOrFocus = async (kind: "uncertainty" | "skills" | "mcp" | "profile" | "commitGate") => {
    if (shell() === "legacyStack") {
      try {
        await vscode.commands.executeCommand("workbench.view.extension.z-left");
        await vscode.commands.executeCommand(`z.${kind}.focus`);
      } catch {
        overlays.open(kind);
      }
      return;
    }
    overlays.open(kind);
  };

  context.subscriptions.push(
    vscode.commands.registerCommand("z.openProfile", () => openOrFocus("profile")),
    vscode.commands.registerCommand("z.focusUncertainty", () => openOrFocus("uncertainty")),
    vscode.commands.registerCommand("z.focusSkills", () => openOrFocus("skills")),
    vscode.commands.registerCommand("z.focusMcp", () => openOrFocus("mcp")),
    vscode.commands.registerCommand("z.focusCommitGate", () => openOrFocus("commitGate"))
  );

  return {
    refreshProfile: () => profile.refresh(),
    refreshChat: () => chat.refresh(),
    openChat: () => chat.show(),
    openProfile: () => void openOrFocus("profile"),
    openGate: () => void openOrFocus("commitGate"),
  };
}
