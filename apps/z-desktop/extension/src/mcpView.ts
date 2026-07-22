/**
 * Phase 10 — MCP connections panel (catalog, connect/test, first-use, sync).
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { zThemeCss } from "./zTheme";

interface McpConnection {
  id?: string;
  serverName?: string;
  server_name?: string;
  displayName?: string;
  display_name?: string;
  connectionType?: string;
  enabled?: boolean;
  status?: string;
  source?: string;
  lastError?: string;
  hasSecrets?: boolean;
}

interface CatalogEntry {
  serverName: string;
  displayName: string;
  connectionType?: string;
  description?: string;
  fields?: Array<{
    key: string;
    label: string;
    secret?: boolean;
    required?: boolean;
  }>;
  oauthStartPath?: string;
}

export class McpViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private connections: McpConnection[] = [];
  private catalog: CatalogEntry[] = [];
  private error: string | null = null;
  private status: string | null = null;
  private selectedServer = "";
  private firstUse: Record<string, boolean> = {};

  constructor(private readonly manager: AppServerManager) {
    manager.onDidChange(() => void this.refresh());
  }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = this.shellHtml();
    webviewView.webview.onDidReceiveMessage((msg) => void this.onMessage(msg));
    webviewView.onDidDispose(() => {
      this.view = undefined;
    });
    void this.refresh();
  }

  private async onMessage(msg: Record<string, unknown>): Promise<void> {
    const type = String(msg?.type || "");
    if (type === "refresh") {
      await this.refresh();
      return;
    }
    if (type === "selectServer") {
      this.selectedServer = String(msg.serverName || "");
      this.post();
      return;
    }
    if (type === "connect") {
      await this.connect(msg);
      return;
    }
    if (type === "test") {
      await this.test(msg);
      return;
    }
    if (type === "disconnect" && msg.id) {
      await this.disconnect(String(msg.id));
      return;
    }
    if (type === "confirmFirstUse" && msg.serverName) {
      await this.confirmFirstUse(String(msg.serverName));
      return;
    }
    if (type === "sync") {
      await this.sync();
      return;
    }
    if (type === "openOauth" && msg.path) {
      const base = process.env.Z_AUTH || process.env.Z_API_BASE || "https://api.z.dev";
      const path = String(msg.path);
      const url = path.startsWith("http") ? path : `${base.replace(/\/$/, "")}${path}`;
      await vscode.env.openExternal(vscode.Uri.parse(url));
      return;
    }
  }

  private async refresh(): Promise<void> {
    this.error = null;
    if (!this.manager.rpc) {
      this.connections = [];
      this.catalog = [];
      this.error = "App-server not connected";
      this.post();
      return;
    }
    try {
      const [listRes, catRes] = await Promise.all([
        this.manager.rpc.request("mcp/list", {}) as Promise<{ connections?: McpConnection[] }>,
        this.manager.rpc.request("mcp/catalog", {}) as Promise<{ catalog?: CatalogEntry[] }>,
      ]);
      this.connections = listRes?.connections || [];
      this.catalog = catRes?.catalog || [];
      if (!this.selectedServer && this.catalog.length) {
        this.selectedServer = this.catalog[0].serverName;
      }
      this.firstUse = {};
      for (const c of this.connections) {
        const name = c.serverName || c.server_name || "";
        if (!name) continue;
        try {
          const st = (await this.manager.rpc.request("mcp/firstUseStatus", {
            serverName: name,
            toolName: "*",
          })) as { needsConfirm?: boolean };
          this.firstUse[name] = Boolean(st?.needsConfirm);
        } catch {
          this.firstUse[name] = true;
        }
      }
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.post();
  }

  private async connect(msg: Record<string, unknown>): Promise<void> {
    if (!this.manager.rpc) return;
    const serverName = String(msg.serverName || this.selectedServer || "");
    const credentials = (msg.credentials || {}) as Record<string, string>;
    try {
      const result = (await this.manager.rpc.request("mcp/connect", {
        serverName,
        credentials,
        syncCloud: true,
      })) as {
        test?: { ok?: boolean; error?: string };
        updated?: boolean;
      };
      const test = result?.test;
      if (test && test.ok === false) {
        this.status = `Connected with warning: ${test.error || "test failed"}`;
      } else {
        this.status = result?.updated ? "Connection updated" : "Connected";
      }
      await this.refresh();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
    }
  }

  private async test(msg: Record<string, unknown>): Promise<void> {
    if (!this.manager.rpc) return;
    try {
      let result: { ok?: boolean; mode?: string; error?: string };
      if (msg.id) {
        result = (await this.manager.rpc.request("mcp/test", {
          id: String(msg.id),
        })) as { ok?: boolean; mode?: string; error?: string };
      } else {
        result = (await this.manager.rpc.request("mcp/test", {
          serverName: String(msg.serverName || this.selectedServer || ""),
          credentials: msg.credentials || {},
          skipPersist: true,
        })) as { ok?: boolean; mode?: string; error?: string };
      }
      this.status = result?.ok
        ? `Test OK (${result.mode || "ok"})`
        : `Test failed: ${result?.error || "unknown"}`;
      this.post();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
    }
  }

  private async disconnect(id: string): Promise<void> {
    if (!this.manager.rpc) return;
    try {
      await this.manager.rpc.request("mcp/disconnect", { id });
      this.status = "Disconnected";
      await this.refresh();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
    }
  }

  private async confirmFirstUse(serverName: string): Promise<void> {
    if (!this.manager.rpc) return;
    try {
      await this.manager.rpc.request("mcp/confirmFirstUse", {
        serverName,
        toolName: "*",
        forever: true,
      });
      this.status = `Trusted ${serverName}`;
      await this.refresh();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
    }
  }

  private async sync(): Promise<void> {
    if (!this.manager.rpc) return;
    try {
      const result = (await this.manager.rpc.request("mcp/sync", {})) as {
        ok?: boolean;
        synced?: number;
        skipped?: number;
        error?: string;
        errors?: string[];
      };
      this.status = result?.ok
        ? `Synced ${result.synced || 0} (skipped ${result.skipped || 0})`
        : `Sync issue: ${result?.error || (result?.errors || []).join("; ") || "failed"}`;
      await this.refresh();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.post();
    }
  }

  private post(): void {
    this.view?.webview.postMessage({
      type: "state",
      connections: this.connections,
      catalog: this.catalog,
      selectedServer: this.selectedServer,
      firstUse: this.firstUse,
      error: this.error,
      status: this.status,
      connected: Boolean(this.manager.rpc),
    });
  }

  private shellHtml(): string {
    return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  ${zThemeCss()}
  body {
    font-family: "IBM Plex Mono", "JetBrains Mono", ui-monospace, monospace;
    padding: 10px;
    margin: 0;
    font-size: 12px;
  }
  h3 { margin: 0 0 8px; color: var(--z-accent-bright); font-weight: 600; }
  .muted { color: var(--z-muted); margin: 0 0 10px; line-height: 1.35; }
  .err { color: var(--z-accent-bright); margin: 8px 0; }
  .ok { color: var(--z-accent); margin: 8px 0; }
  .conn {
    border-top: 1px solid rgba(201,106,43,0.25);
    padding: 8px 0;
  }
  .name { font-weight: 600; color: var(--z-text); }
  .meta { color: var(--z-muted); font-size: 11px; margin-top: 2px; }
  .badge {
    display: inline-block;
    margin-right: 6px;
    color: var(--z-accent);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  label { display: block; margin: 8px 0 4px; color: var(--z-accent); font-size: 11px; }
  select, input {
    width: 100%;
    box-sizing: border-box;
    background: #121212;
    color: var(--z-text);
    border: 1px solid rgba(201,106,43,0.35);
    padding: 6px 8px;
    font: inherit;
  }
  .actions { margin-top: 10px; }
  button { margin: 4px 6px 4px 0; }
  .warn { color: var(--z-accent-bright); font-size: 11px; margin-top: 4px; }
</style>
</head>
<body>
  <h3>MCP</h3>
  <p class="muted">Connect tools locally. First use requires trust (D9).</p>
  <div id="banner"></div>
  <div id="list"></div>
  <div id="form"></div>
  <div class="actions">
    <button data-cmd="refresh">Refresh</button>
    <button class="secondary" data-cmd="sync">Sync to cloud</button>
  </div>
<script>
  const vscode = acquireVsCodeApi();
  let state = { connections: [], catalog: [], selectedServer: "", firstUse: {}, error: null, status: null };

  for (const btn of document.querySelectorAll('button[data-cmd]')) {
    btn.addEventListener('click', () => vscode.postMessage({ type: btn.dataset.cmd }));
  }

  window.addEventListener('message', (event) => {
    const msg = event.data;
    if (msg?.type !== 'state') return;
    state = msg;
    render();
  });

  function esc(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function render() {
    const banner = document.getElementById('banner');
    banner.innerHTML = '';
    if (state.error) banner.innerHTML += '<div class="err">' + esc(state.error) + '</div>';
    if (state.status) banner.innerHTML += '<div class="ok">' + esc(state.status) + '</div>';

    const list = document.getElementById('list');
    if (!state.connections.length) {
      list.innerHTML = '<p class="muted">No connections yet.</p>';
    } else {
      list.innerHTML = state.connections.map((c) => {
        const name = c.serverName || c.server_name || '';
        const disp = c.displayName || c.display_name || name;
        const needs = state.firstUse[name];
        return '<div class="conn" data-id="' + esc(c.id || '') + '" data-server="' + esc(name) + '">' +
          '<div class="name">' + esc(disp) + '</div>' +
          '<div class="meta"><span class="badge">' + esc(c.source || 'local') + '</span>' +
          esc(c.status || '') + (c.enabled === false ? ' · disabled' : '') + '</div>' +
          (c.lastError ? '<div class="warn">' + esc(c.lastError) + '</div>' : '') +
          (needs ? '<div class="warn">Needs first-use trust</div><button data-act="trust">Trust this server</button>' : '') +
          '<div class="actions"><button class="secondary" data-act="testId">Test</button>' +
          '<button class="secondary" data-act="disconnect">Disconnect</button></div></div>';
      }).join('');
      for (const el of list.querySelectorAll('.conn')) {
        el.querySelector('[data-act="disconnect"]')?.addEventListener('click', () => {
          vscode.postMessage({ type: 'disconnect', id: el.dataset.id });
        });
        el.querySelector('[data-act="testId"]')?.addEventListener('click', () => {
          vscode.postMessage({ type: 'test', id: el.dataset.id });
        });
        el.querySelector('[data-act="trust"]')?.addEventListener('click', () => {
          vscode.postMessage({ type: 'confirmFirstUse', serverName: el.dataset.server });
        });
      }
    }

    const form = document.getElementById('form');
    const cat = state.catalog || [];
    const selected = cat.find((e) => e.serverName === state.selectedServer) || cat[0];
    if (!selected) {
      form.innerHTML = '';
      return;
    }
    let html = '<label>Add from catalog</label><select id="serverSel">';
    html += cat.map((e) =>
      '<option value="' + esc(e.serverName) + '"' +
      (e.serverName === selected.serverName ? ' selected' : '') + '>' +
      esc(e.displayName) + '</option>'
    ).join('');
    html += '</select>';
    if (selected.description) html += '<p class="muted">' + esc(selected.description) + '</p>';
    if (selected.connectionType === 'oauth') {
      html += '<button data-act="oauth">Connect via web (OAuth)</button>';
    } else {
      for (const f of (selected.fields || [])) {
        html += '<label>' + esc(f.label || f.key) + (f.required ? ' *' : '') + '</label>';
        html += '<input data-field="' + esc(f.key) + '" type="' + (f.secret ? 'password' : 'text') + '" />';
      }
      html += '<div class="actions"><button data-act="testNew">Test</button><button data-act="connect">Connect</button></div>';
    }
    form.innerHTML = html;
    form.querySelector('#serverSel')?.addEventListener('change', (ev) => {
      vscode.postMessage({ type: 'selectServer', serverName: ev.target.value });
    });
    form.querySelector('[data-act="oauth"]')?.addEventListener('click', () => {
      vscode.postMessage({ type: 'openOauth', path: selected.oauthStartPath || '/v1/mcp/oauth/start?server_name=' + selected.serverName });
    });
    form.querySelector('[data-act="connect"]')?.addEventListener('click', () => {
      vscode.postMessage({ type: 'connect', serverName: selected.serverName, credentials: collectCreds() });
    });
    form.querySelector('[data-act="testNew"]')?.addEventListener('click', () => {
      vscode.postMessage({ type: 'test', serverName: selected.serverName, credentials: collectCreds() });
    });
  }

  function collectCreds() {
    const out = {};
    for (const input of document.querySelectorAll('input[data-field]')) {
      if (input.value) out[input.dataset.field] = input.value;
    }
    return out;
  }
</script>
</body>
</html>`;
  }
}
