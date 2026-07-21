/**
 * Phase 4 — Cursor-style Chat panel: type a prompt, stream the turn, answer WaitingInput.
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";

type ChatRole = "user" | "assistant" | "system";

interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
}

interface WaitingPrompt {
  turnId?: string | null;
  requestId: string;
  kind: string;
  question: string;
  subject?: string | null;
  options?: string[] | null;
  default?: string;
}

export class ChatViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private messages: ChatMessage[] = [];
  private busyLabel = "";
  private waiting: WaitingPrompt | null = null;
  private streamingAssistantId: string | null = null;
  private threadId = "default";
  private disposed = false;

  constructor(private readonly manager: AppServerManager) {
    manager.onNotification((method, params) => {
      void this.onNotification(method, params as Record<string, unknown>);
    });
    manager.onDidChange(() => this.postState());
  }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = this.html();
    webviewView.webview.onDidReceiveMessage((msg) => void this.onMessage(msg));
    webviewView.onDidDispose(() => {
      this.view = undefined;
    });
    this.postState();
  }

  refresh(): void {
    this.postState();
  }

  private async onMessage(msg: { type?: string; text?: string; response?: string; changeText?: string }) {
    if (!msg?.type) {
      return;
    }
    if (msg.type === "send") {
      await this.sendPrompt(String(msg.text || ""));
      return;
    }
    if (msg.type === "respond" && this.waiting) {
      const requestId = this.waiting.requestId;
      const response = msg.response;
      const text = msg.changeText;
      this.waiting = null;
      this.postState();
      try {
        await this.manager.rpc?.request("turn/respond", {
          requestId,
          response,
          text,
          threadId: this.threadId,
        });
      } catch (err) {
        vscode.window.showErrorMessage(
          `turn/respond failed: ${err instanceof Error ? err.message : err}`
        );
      }
      return;
    }
    if (msg.type === "cancel") {
      try {
        await this.manager.rpc?.request("turn/cancel", { threadId: this.threadId });
      } catch {
        /* ignore */
      }
      this.waiting = null;
      this.busyLabel = "";
      this.postState();
      return;
    }
    if (msg.type === "clear") {
      this.messages = [];
      this.streamingAssistantId = null;
      this.waiting = null;
      this.busyLabel = "";
      this.postState();
    }
  }

  private async sendPrompt(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed) {
      return;
    }
    if (!this.manager.rpc) {
      try {
        await this.manager.ensureConnected();
      } catch (err) {
        vscode.window.showErrorMessage(
          `Connect to Z first: ${err instanceof Error ? err.message : err}`
        );
        return;
      }
    }
    this.messages.push({
      id: `u-${Date.now()}`,
      role: "user",
      text: trimmed,
    });
    this.streamingAssistantId = null;
    this.busyLabel = "Working…";
    this.postState();
    try {
      const result = (await this.manager.rpc!.request("turn/start", {
        text: trimmed,
        threadId: this.threadId,
      })) as { turnId?: string; queued?: boolean; accepted?: boolean };
      if (result.queued) {
        this.messages.push({
          id: `s-${Date.now()}`,
          role: "system",
          text: "Queued — will run after the current turn.",
        });
        this.postState();
      }
    } catch (err) {
      this.busyLabel = "";
      this.messages.push({
        id: `e-${Date.now()}`,
        role: "system",
        text: `Could not start turn: ${err instanceof Error ? err.message : err}`,
      });
      this.postState();
    }
  }

  private onNotification(method: string, params: Record<string, unknown>): void {
    if (this.disposed) {
      return;
    }
    if (method === "turn/busy") {
      const label = String(params.label || params.phase || "");
      const state = String(params.state || "");
      if (state === "idle") {
        this.busyLabel = "";
      } else if (state === "waiting_input") {
        this.busyLabel = label || "Waiting for your reply…";
      } else {
        this.busyLabel = label || "Working…";
      }
      this.postState();
      return;
    }
    if (method === "item/agentMessage/delta") {
      const chunk = String(params.text || "");
      if (!chunk) {
        return;
      }
      if (!this.streamingAssistantId) {
        this.streamingAssistantId = `a-${Date.now()}`;
        this.messages.push({
          id: this.streamingAssistantId,
          role: "assistant",
          text: chunk,
        });
      } else {
        const msg = this.messages.find((m) => m.id === this.streamingAssistantId);
        if (msg) {
          msg.text += chunk;
        }
      }
      this.postState();
      return;
    }
    if (method === "turn/waiting_input") {
      this.waiting = {
        turnId: (params.turnId as string) || null,
        requestId: String(params.requestId || ""),
        kind: String(params.kind || "confirm"),
        question: String(params.question || "Confirm?"),
        subject: (params.subject as string) || null,
        options: (params.options as string[]) || null,
        default: String(params.default || ""),
      };
      this.busyLabel = `Waiting — ${this.waiting.kind}`;
      this.postState();
      return;
    }
    if (method === "turn/log") {
      const level = String(params.level || "info");
      const text = String(params.text || "").trim();
      if (!text) {
        return;
      }
      // Keep chat clean: only surface warnings/errors as system lines
      if (level === "warning" || level === "error") {
        this.messages.push({
          id: `log-${Date.now()}`,
          role: "system",
          text: text.slice(0, 2000),
        });
        this.postState();
      }
      return;
    }
    if (method === "turn/completed") {
      this.busyLabel = "";
      this.waiting = null;
      this.streamingAssistantId = null;
      if (params.ok === false && params.interrupted) {
        this.messages.push({
          id: `sys-${Date.now()}`,
          role: "system",
          text: "Turn interrupted.",
        });
      }
      this.postState();
      return;
    }
    if (method === "turn/error") {
      this.messages.push({
        id: `err-${Date.now()}`,
        role: "system",
        text: `Error: ${String(params.message || "turn failed")}`,
      });
      this.busyLabel = "";
      this.postState();
    }
  }

  private postState(): void {
    if (!this.view) {
      return;
    }
    this.view.webview.postMessage({
      type: "state",
      connection: this.manager.connectionState,
      busyLabel: this.busyLabel,
      waiting: this.waiting,
      messages: this.messages,
    });
  }

  private html(): string {
    return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  :root {
    color-scheme: light dark;
  }
  html, body {
    height: 100%;
    margin: 0;
    padding: 0;
    font-family: var(--vscode-font-family);
    color: var(--vscode-foreground);
    background: var(--vscode-sideBar-background);
  }
  #app {
    display: flex;
    flex-direction: column;
    height: 100vh;
    box-sizing: border-box;
  }
  #status {
    padding: 8px 12px;
    font-size: 12px;
    opacity: 0.85;
    border-bottom: 1px solid var(--vscode-panel-border, rgba(127,127,127,0.3));
    min-height: 18px;
  }
  #status.busy { opacity: 1; }
  #msgs {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
  }
  .msg { margin: 0 0 14px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; }
  .msg .role {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.6;
    margin-bottom: 4px;
  }
  .msg.user .bubble { color: var(--vscode-foreground); }
  .msg.assistant .bubble { }
  .msg.system .bubble { opacity: 0.8; font-size: 12px; }
  #waiting {
    display: none;
    padding: 10px 12px;
    border-top: 1px solid var(--vscode-panel-border, rgba(127,127,127,0.3));
    background: var(--vscode-inputValidation-infoBackground, transparent);
  }
  #waiting.show { display: block; }
  #waiting .q { font-weight: 600; margin-bottom: 6px; }
  #waiting .subject {
    max-height: 140px;
    overflow: auto;
    font-size: 12px;
    opacity: 0.85;
    margin-bottom: 8px;
    white-space: pre-wrap;
  }
  #waiting .actions { display: flex; flex-wrap: wrap; gap: 6px; }
  #composer {
    border-top: 1px solid var(--vscode-panel-border, rgba(127,127,127,0.3));
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  textarea {
    width: 100%;
    min-height: 72px;
    resize: vertical;
    box-sizing: border-box;
    background: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, transparent);
    padding: 8px;
    font-family: inherit;
    font-size: 13px;
  }
  .row { display: flex; gap: 8px; align-items: center; }
  button {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none;
    padding: 6px 12px;
    cursor: pointer;
  }
  button.secondary {
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
  }
  button:disabled { opacity: 0.5; cursor: default; }
  .hint { font-size: 11px; opacity: 0.6; }
</style>
</head>
<body>
  <div id="app">
    <div id="status">Z Chat</div>
    <div id="msgs"></div>
    <div id="waiting">
      <div class="q" id="wq"></div>
      <div class="subject" id="wsubject"></div>
      <div class="actions" id="wactions"></div>
      <div id="wchange" style="display:none;margin-top:8px">
        <textarea id="wchangetext" placeholder="Describe the plan change…"></textarea>
        <div class="row" style="margin-top:6px">
          <button id="wchangeSend">Send change</button>
        </div>
      </div>
    </div>
    <div id="composer">
      <textarea id="input" placeholder="Message Z…"></textarea>
      <div class="row">
        <button id="send">Send</button>
        <button class="secondary" id="cancel">Stop</button>
        <button class="secondary" id="clear">Clear</button>
        <span class="hint">Enter to send · Shift+Enter newline</span>
      </div>
    </div>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    const msgsEl = document.getElementById('msgs');
    const statusEl = document.getElementById('status');
    const waitingEl = document.getElementById('waiting');
    const input = document.getElementById('input');
    let waiting = null;

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;')
        .replace(/</g,'&lt;')
        .replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;');
    }

    function renderMessages(messages) {
      msgsEl.innerHTML = messages.map(m => {
        const role = m.role === 'user' ? 'You' : m.role === 'assistant' ? 'Z' : 'System';
        return '<div class="msg ' + m.role + '"><div class="role">' + role + '</div><div class="bubble">' + escapeHtml(m.text) + '</div></div>';
      }).join('');
      msgsEl.scrollTop = msgsEl.scrollHeight;
    }

    function renderWaiting(w) {
      waiting = w;
      const box = waitingEl;
      if (!w) {
        box.classList.remove('show');
        return;
      }
      box.classList.add('show');
      document.getElementById('wq').textContent = w.question || 'Confirm?';
      document.getElementById('wsubject').textContent = w.subject || '';
      const actions = document.getElementById('wactions');
      actions.innerHTML = '';
      const opts = w.options && w.options.length ? w.options : ['yes', 'no'];
      for (const opt of opts) {
        const b = document.createElement('button');
        b.textContent = opt;
        if (opt !== 'yes' && opt !== 'Yes') b.className = 'secondary';
        b.onclick = () => {
          if (opt === 'change' || opt === 'Change') {
            document.getElementById('wchange').style.display = 'block';
            return;
          }
          vscode.postMessage({ type: 'respond', response: opt });
        };
        actions.appendChild(b);
      }
      document.getElementById('wchange').style.display = 'none';
    }

    window.addEventListener('message', (event) => {
      const data = event.data || {};
      if (data.type !== 'state') return;
      const conn = data.connection || 'disconnected';
      const busy = data.busyLabel || '';
      statusEl.textContent = busy
        ? busy
        : (conn === 'connected' ? 'Z · ready' : 'Z · ' + conn);
      statusEl.className = busy ? 'busy' : '';
      renderMessages(data.messages || []);
      renderWaiting(data.waiting || null);
    });

    document.getElementById('send').onclick = () => {
      const text = input.value;
      input.value = '';
      vscode.postMessage({ type: 'send', text });
    };
    document.getElementById('cancel').onclick = () => vscode.postMessage({ type: 'cancel' });
    document.getElementById('clear').onclick = () => vscode.postMessage({ type: 'clear' });
    document.getElementById('wchangeSend').onclick = () => {
      const t = document.getElementById('wchangetext').value;
      vscode.postMessage({ type: 'respond', response: 'change', changeText: t });
      document.getElementById('wchangetext').value = '';
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        document.getElementById('send').click();
      }
    });
  </script>
</body>
</html>`;
  }
}
