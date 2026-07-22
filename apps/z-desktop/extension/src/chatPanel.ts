/**
 * Agent-first main Chat — center editor webview (not a sidebar).
 * Cursor-style queued prompt preview while the agent is mid-turn.
 */

import * as vscode from "vscode";
import { AppServerManager } from "./appServerManager";
import { zThemeCss } from "./zTheme";

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

interface QueueState {
  queueLen: number;
  items: string[];
  preview: string | null;
}

export class MainChatPanel {
  public static readonly viewType = "z.chatPanel";

  private panel: vscode.WebviewPanel | undefined;
  private messages: ChatMessage[] = [];
  private busyLabel = "";
  private waiting: WaitingPrompt | null = null;
  private streamingAssistantId: string | null = null;
  private threadId = "default";
  private queue: QueueState = { queueLen: 0, items: [], preview: null };
  private disposed = false;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly manager: AppServerManager
  ) {
    manager.onNotification((method, params) => {
      void this.onNotification(method, params as Record<string, unknown>);
    });
    manager.onDidChange(() => this.postState());
  }

  /** Open (or reveal) Chat as the center main interface. */
  show(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.One, false);
      this.postState();
      return;
    }

    this.panel = vscode.window.createWebviewPanel(
      MainChatPanel.viewType,
      "Z",
      { viewColumn: vscode.ViewColumn.One, preserveFocus: false },
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [this.context.extensionUri],
      }
    );
    this.panel.iconPath = vscode.Uri.joinPath(this.context.extensionUri, "media", "z-activity.svg");
    this.panel.webview.html = this.html();
    this.panel.webview.onDidReceiveMessage(
      (msg) => void this.onMessage(msg),
      undefined,
      this.context.subscriptions
    );
    this.panel.onDidDispose(
      () => {
        this.panel = undefined;
      },
      undefined,
      this.context.subscriptions
    );
    this.postState();
  }

  refresh(): void {
    this.postState();
  }

  dispose(): void {
    this.disposed = true;
    this.panel?.dispose();
  }

  private async onMessage(msg: {
    type?: string;
    text?: string;
    response?: string;
    changeText?: string;
  }) {
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
      this.queue = { queueLen: 0, items: [], preview: null };
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

    const agentBusy = Boolean(this.busyLabel) || this.queue.queueLen > 0;

    try {
      const result = (await this.manager.rpc!.request("turn/start", {
        text: trimmed,
        threadId: this.threadId,
      })) as {
        turnId?: string;
        queued?: boolean;
        accepted?: boolean;
        queueLen?: number;
        items?: string[];
        preview?: string | null;
      };

      if (result.queued) {
        // Do not interrupt — show Cursor-style queued preview (not a chat bubble yet).
        this.setQueueFromPayload(result);
        this.postState();
        return;
      }

      this.messages.push({
        id: `u-${Date.now()}`,
        role: "user",
        text: trimmed,
      });
      this.streamingAssistantId = null;
      this.busyLabel = "Working…";
      this.queue = { queueLen: 0, items: [], preview: null };
      this.postState();
    } catch (err) {
      if (!agentBusy) {
        this.busyLabel = "";
      }
      this.messages.push({
        id: `e-${Date.now()}`,
        role: "system",
        text: `Could not start turn: ${err instanceof Error ? err.message : err}`,
      });
      this.postState();
    }
  }

  private setQueueFromPayload(params: {
    queueLen?: number;
    items?: string[];
    preview?: string | null;
  }): void {
    const items = Array.isArray(params.items)
      ? params.items.map((x) => String(x))
      : [];
    const queueLen = typeof params.queueLen === "number" ? params.queueLen : items.length;
    let preview = params.preview != null ? String(params.preview) : null;
    if (!preview && items.length) {
      const one = items[0].replace(/\s+/g, " ").trim();
      preview = one.length > 72 ? `▶ queued: ${one.slice(0, 71)}…` : `▶ queued: ${one}`;
    }
    this.queue = { queueLen, items, preview: queueLen ? preview : null };
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
      if (typeof params.queueLen === "number" && params.queueLen === 0) {
        this.queue = { queueLen: 0, items: [], preview: null };
      }
      this.postState();
      return;
    }
    if (method === "turn/queued") {
      this.setQueueFromPayload(params as { queueLen?: number; items?: string[]; preview?: string | null });
      this.postState();
      return;
    }
    if (method === "turn/started") {
      const text = String(params.text || "").trim();
      const fromQueue = Boolean(params.fromQueue);
      if (fromQueue && text) {
        // Promote queued preview into the transcript when the follow-up starts.
        this.messages.push({
          id: `u-${Date.now()}`,
          role: "user",
          text,
        });
        this.streamingAssistantId = null;
        this.busyLabel = "Working…";
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
    if (!this.panel) {
      return;
    }
    this.panel.webview.postMessage({
      type: "state",
      connection: this.manager.connectionState,
      busyLabel: this.busyLabel,
      waiting: this.waiting,
      messages: this.messages,
      queue: this.queue,
    });
  }

  private html(): string {
    return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  ${zThemeCss()}
  html, body {
    height: 100%; margin: 0; padding: 0;
    font-family: "IBM Plex Mono", "JetBrains Mono", "SF Mono", ui-monospace, monospace;
  }
  #app {
    display: flex; flex-direction: column; height: 100vh; box-sizing: border-box;
    max-width: 820px; margin: 0 auto; width: 100%;
    background:
      radial-gradient(ellipse 80% 50% at 50% -10%, rgba(201,106,43,0.18), transparent 55%),
      var(--z-bg);
  }
  #brand {
    padding: 20px 20px 6px;
    font-size: 28px; font-weight: 700; letter-spacing: -0.03em;
    color: var(--z-accent-bright);
  }
  #status {
    padding: 0 20px 12px; font-size: 12px; color: var(--z-muted); min-height: 16px;
  }
  #status.busy { color: var(--z-accent); }
  #msgs {
    flex: 1; overflow-y: auto; padding: 8px 20px 16px;
  }
  .msg { margin: 0 0 16px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
  .msg .role {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--z-accent); margin-bottom: 4px;
  }
  .msg.user .role { color: var(--z-muted); }
  .msg.system .bubble { color: var(--z-muted); font-size: 12px; }
  #waiting {
    display: none; margin: 0 16px; padding: 12px 14px;
    border: 1px solid var(--z-accent-dim);
    background: var(--z-raised);
  }
  #waiting.show { display: block; }
  #waiting .q { font-weight: 600; margin-bottom: 6px; color: var(--z-accent-bright); }
  #waiting .subject {
    max-height: 160px; overflow: auto; font-size: 12px; color: var(--z-muted);
    margin-bottom: 8px; white-space: pre-wrap;
  }
  #waiting .actions { display: flex; flex-wrap: wrap; gap: 6px; }
  #queue {
    display: none; margin: 8px 16px 0; padding: 10px 12px;
    border: 1px dashed var(--z-accent);
    background: var(--z-surface);
    font-size: 12px;
  }
  #queue.show { display: block; }
  #queue .label {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--z-accent); margin-bottom: 4px;
  }
  #queue .preview { white-space: pre-wrap; word-break: break-word; color: var(--z-text); }
  #queue .more { color: var(--z-muted); margin-top: 4px; font-size: 11px; }
  #composer {
    border-top: 1px solid var(--z-border);
    padding: 12px 16px 16px; display: flex; flex-direction: column; gap: 8px;
    background: var(--z-surface);
  }
  textarea {
    width: 100%; min-height: 88px; resize: vertical; box-sizing: border-box;
    padding: 10px; font-family: inherit; font-size: 14px;
  }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .hint { font-size: 11px; color: var(--z-muted); }
</style>
</head>
<body>
  <div id="app">
    <div id="brand">Z</div>
    <div id="status">Ready</div>
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
    <div id="queue">
      <div class="label">Queued — runs after current turn</div>
      <div class="preview" id="qpreview"></div>
      <div class="more" id="qmore"></div>
    </div>
    <div id="composer">
      <textarea id="input" placeholder="Prompt the agent…"></textarea>
      <div class="row">
        <button id="send">Send</button>
        <button class="secondary" id="cancel">Stop</button>
        <button class="secondary" id="clear">Clear</button>
        <span class="hint">Enter to send · Shift+Enter newline · busy → queues</span>
      </div>
    </div>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    const msgsEl = document.getElementById('msgs');
    const statusEl = document.getElementById('status');
    const waitingEl = document.getElementById('waiting');
    const queueEl = document.getElementById('queue');
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

    function renderQueue(q) {
      if (!q || !q.queueLen) {
        queueEl.classList.remove('show');
        return;
      }
      queueEl.classList.add('show');
      const preview = q.preview || (q.items && q.items[0] ? ('▶ queued: ' + q.items[0]) : 'Queued follow-up');
      document.getElementById('qpreview').textContent = preview;
      const extra = Math.max(0, (q.queueLen || 0) - 1);
      document.getElementById('qmore').textContent = extra
        ? ('+' + extra + ' more queued')
        : (q.queueLen + ' in queue');
    }

    function renderWaiting(w) {
      waiting = w;
      if (!w) {
        waitingEl.classList.remove('show');
        return;
      }
      waitingEl.classList.add('show');
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
        : (conn === 'connected' ? 'Agent ready — you prompt, Z programs' : 'Z · ' + conn);
      statusEl.className = busy ? 'busy' : '';
      renderMessages(data.messages || []);
      renderWaiting(data.waiting || null);
      renderQueue(data.queue || null);
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
