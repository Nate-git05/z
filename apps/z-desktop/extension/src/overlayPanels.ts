/**
 * Chat-first shell — open secondary surfaces as editor overlays (not a stacked sidebar).
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { UncertaintyTreeProvider } from "./uncertaintyView";
import { SkillsViewProvider } from "./skillsView";
import { McpViewProvider } from "./mcpView";
import { CommitGateProvider } from "./commitGateView";
import { ProfileViewProvider } from "./profileView";

type OverlayKind = "uncertainty" | "skills" | "mcp" | "profile" | "commitGate";

const TITLES: Record<OverlayKind, string> = {
  uncertainty: "Uncertainty",
  skills: "Skills",
  mcp: "MCP",
  profile: "Profile",
  commitGate: "Commit Gate",
};

export class OverlayPanelHost {
  private panels = new Map<OverlayKind, vscode.WebviewPanel>();

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly manager: AppServerManager,
    private readonly providers: {
      uncertainty: UncertaintyTreeProvider;
      skills: SkillsViewProvider;
      mcp: McpViewProvider;
      profile: ProfileViewProvider;
      commitGate: CommitGateProvider;
    }
  ) {}

  open(kind: OverlayKind): void {
    const existing = this.panels.get(kind);
    if (existing) {
      existing.reveal(vscode.ViewColumn.Beside, false);
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      `z.overlay.${kind}`,
      TITLES[kind],
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: false },
      { enableScripts: true, retainContextWhenHidden: true }
    );
    this.panels.set(kind, panel);
    panel.onDidDispose(() => this.panels.delete(kind));

    const provider = this.providers[kind];
    // WebviewViewProvider.resolveWebviewView expects a WebviewView — adapt via shim.
    const shim = {
      webview: panel.webview,
      onDidDispose: panel.onDidDispose,
      onDidChangeVisibility: () => ({ dispose() {} }),
      show: () => panel.reveal(),
      visible: true,
      viewType: `z.overlay.${kind}`,
      title: TITLES[kind],
      description: undefined,
      badge: undefined,
    } as unknown as vscode.WebviewView;
    provider.resolveWebviewView(shim);
  }
}
