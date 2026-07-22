/**
 * P5 — first-run wizard when the Z engine (`z` CLI / app-server) is missing.
 */

import * as vscode from "vscode";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as path from "path";

export function resolveBundledZBinary(extensionPath: string): string | undefined {
  const candidates = [
    path.join(extensionPath, "..", "z-runtime", "bin", "z"),
    path.join(extensionPath, "..", "z-runtime", "z"),
    path.join(extensionPath, "z-runtime", "bin", "z"),
    path.join(extensionPath, "resources", "z-runtime", "bin", "z"),
  ];
  for (const c of candidates) {
    try {
      if (fs.existsSync(c)) {
        return c;
      }
    } catch {
      /* ignore */
    }
  }
  return undefined;
}

export function findZOnPath(): string | undefined {
  try {
    const which = process.platform === "win32" ? "where" : "which";
    const out = execFileSync(which, ["z"], { encoding: "utf8" }).trim();
    const first = out.split(/\r?\n/)[0];
    return first || undefined;
  } catch {
    return undefined;
  }
}

export async function ensureEngineOrWizard(
  context: vscode.ExtensionContext
): Promise<string | undefined> {
  const cfg = vscode.workspace.getConfiguration("z");
  const configured = (cfg.get<string>("zBinary") || "").trim();
  if (configured && fs.existsSync(configured)) {
    return configured;
  }

  const bundled = resolveBundledZBinary(context.extensionPath);
  if (bundled) {
    return bundled;
  }

  const onPath = findZOnPath();
  if (onPath) {
    return onPath;
  }

  if (!cfg.get<boolean>("promptInstallEngine", true)) {
    return undefined;
  }

  const pick = await vscode.window.showWarningMessage(
    "Z engine not found. Install the `z` CLI so Chat can start the app-server.",
    "Install instructions",
    "Locate z binary…",
    "Not now"
  );

  if (pick === "Locate z binary…") {
    const uris = await vscode.window.showOpenDialog({
      canSelectMany: false,
      openLabel: "Use this z binary",
      title: "Locate the z CLI",
    });
    const file = uris?.[0]?.fsPath;
    if (file) {
      await cfg.update("zBinary", file, vscode.ConfigurationTarget.Global);
      vscode.window.showInformationMessage(`Z binary set to ${file}`);
      return file;
    }
    return undefined;
  }

  if (pick === "Install instructions") {
    const doc = await vscode.workspace.openTextDocument({
      language: "markdown",
      content: `# Install the Z engine

The editor UI needs the \`z\` CLI (app-server) on your machine.

## Quick install (from this repo)

\`\`\`bash
cd /path/to/z
pip install -e ".[web]"
which z
z app-server --help
\`\`\`

## Then

1. Command Palette → **Z: Reconnect app-server**
2. Or set **Z: zBinary** in Settings to the full path from \`which z\`

## Downloads

- Extension VSIX: GitHub Actions artifact \`z-editor-*.vsix\`
- Full Z Editor app: see apps/z-desktop/INSTALL.md when unsigned builds are published
`,
    });
    await vscode.window.showTextDocument(doc, { preview: true });
  }

  return undefined;
}
