/**
 * Phase 3c — sign-in UX in the workbench + z-editor:// deep links.
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { LoginStatus } from "./appServerClient";

export function registerAuthCommands(
  context: vscode.ExtensionContext,
  manager: AppServerManager,
  onAuthChanged: () => void
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("z.signIn", () => signIn(manager, onAuthChanged)),
    vscode.commands.registerCommand("z.signOut", () => signOut(manager, onAuthChanged)),
    vscode.commands.registerCommand("z.cancelSignIn", async () => {
      const rpc = manager.rpc;
      if (!rpc) {
        return;
      }
      await rpc.request("auth/loginCancel");
      vscode.window.showInformationMessage("Z sign-in cancelled.");
      onAuthChanged();
    }),
    vscode.window.registerUriHandler({
      handleUri(uri: vscode.Uri): void {
        void handleDeepLink(uri, manager, onAuthChanged);
      },
    })
  );
}

async function ensureRpc(manager: AppServerManager) {
  if (!manager.rpc) {
    await manager.ensureConnected();
  }
  if (!manager.rpc) {
    throw new Error("Not connected to z-app-server");
  }
  return manager.rpc;
}

export async function signIn(
  manager: AppServerManager,
  onAuthChanged: () => void,
  presetMethod?: "google" | "z"
): Promise<void> {
  let method = presetMethod;
  if (!method) {
    const pick = await vscode.window.showQuickPick(
      [
        { label: "Google", description: "Continue with Google", method: "google" as const },
        { label: "Z", description: "Email / Z account", method: "z" as const },
      ],
      { placeHolder: "Sign in to Z", title: "Z Sign In" }
    );
    if (!pick) {
      return;
    }
    method = pick.method;
  }

  await beginLoginAndPoll(manager, onAuthChanged, method, "signin");
}

async function beginLoginAndPoll(
  manager: AppServerManager,
  onAuthChanged: () => void,
  method: "google" | "z",
  intent: "signin" | "signup"
): Promise<void> {
  const rpc = await ensureRpc(manager);
  const started = (await rpc.request("auth/loginStart", {
    method,
    intent,
    openBrowser: false,
  })) as LoginStatus & { started?: boolean; loginUrl?: string };

  const url = started.loginUrl;
  if (!url) {
    vscode.window.showErrorMessage("Z sign-in did not return a login URL.");
    return;
  }

  await vscode.env.openExternal(vscode.Uri.parse(url));
  vscode.window.showInformationMessage(
    "Complete sign-in in your browser. Z Editor will update when you're done."
  );

  const deadline = Date.now() + 5 * 60 * 1000;
  while (Date.now() < deadline) {
    await sleep(1500);
    if (!manager.rpc) {
      break;
    }
    const st = (await manager.rpc.request("auth/loginStatus")) as LoginStatus;
    if (st.status === "succeeded") {
      vscode.window.showInformationMessage(
        `Signed in to Z${st.email ? ` as ${st.email}` : ""}.`
      );
      onAuthChanged();
      return;
    }
    if (st.status === "failed") {
      vscode.window.showErrorMessage(st.error || "Z sign-in failed.");
      onAuthChanged();
      return;
    }
    if (st.status === "cancelled" || st.status === "idle") {
      onAuthChanged();
      return;
    }
  }
  vscode.window.showWarningMessage("Timed out waiting for Z sign-in.");
  onAuthChanged();
}

async function signOut(
  manager: AppServerManager,
  onAuthChanged: () => void
): Promise<void> {
  const rpc = await ensureRpc(manager);
  await rpc.request("auth/logout");
  vscode.window.showInformationMessage("Signed out of Z.");
  onAuthChanged();
}

async function handleDeepLink(
  uri: vscode.Uri,
  manager: AppServerManager,
  onAuthChanged: () => void
): Promise<void> {
  // z-editor://signin?method=google
  // z-editor://auth/complete
  const host = (uri.authority || "").toLowerCase();
  const path = (uri.path || "").replace(/^\//, "").toLowerCase();
  const params = new URLSearchParams(uri.query);
  const route = `${host}/${path}`.replace(/\/+/g, "/");

  if (host === "signin" || path === "signin" || route.includes("signin")) {
    const method = (params.get("method") || "google").toLowerCase() === "z" ? "z" : "google";
    await signIn(manager, onAuthChanged, method);
    return;
  }

  if (
    host === "auth" ||
    path === "auth/complete" ||
    path === "complete" ||
    host === "auth-complete"
  ) {
    onAuthChanged();
    try {
      const auth = await manager.authStatus();
      if (auth?.authenticated) {
        vscode.window.showInformationMessage(
          `Z session ready${auth.email ? `: ${auth.email}` : ""}`
        );
      }
    } catch {
      /* ignore */
    }
    return;
  }

  // Phase 13 — z-editor://mcp/oauth/done?server=github&status=ok
  if (
    host === "mcp" ||
    path.startsWith("mcp/oauth") ||
    route.includes("mcp/oauth")
  ) {
    const server = params.get("server") || params.get("serverName") || "";
    const status = (params.get("status") || "ok").toLowerCase();
    try {
      await ensureRpc(manager);
      if (manager.rpc) {
        await manager.rpc.request("mcp/sync", {});
      }
    } catch {
      /* sync best-effort */
    }
    onAuthChanged();
    if (status === "ok") {
      vscode.window.showInformationMessage(
        `MCP connected${server ? `: ${server}` : ""}. Open the MCP panel to trust & test.`
      );
      try {
        await vscode.commands.executeCommand("z.focusMcp");
      } catch {
        /* ignore */
      }
    } else {
      vscode.window.showWarningMessage(
        `MCP OAuth ${status}${server ? ` (${server})` : ""}`
      );
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
