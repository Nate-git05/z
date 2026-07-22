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
  kind?: "tool" | "error" | "info";
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

type ActivityPhase =
  | "idle"
  | "thinking"
  | "planning"
  | "editing"
  | "searching"
  | "running"
  | "mcp"
  | "choosing_model"
  | "waiting"
  | "queued";

interface ActivityStripState {
  phase: ActivityPhase;
  modelId?: string | null;
  editingFiles: number;
  exploredFiles: number;
  searches: number;
  commands: number;
  mcpCalls: number;
  linesAdded: number;
  linesRemoved: number;
  fileNames?: string[];
  /** True while a turn is active (or settling). */
  live: boolean;
}

function emptyActivity(): ActivityStripState {
  return {
    phase: "idle",
    modelId: null,
    editingFiles: 0,
    exploredFiles: 0,
    searches: 0,
    commands: 0,
    mcpCalls: 0,
    linesAdded: 0,
    linesRemoved: 0,
    fileNames: [],
    live: false,
  };
}

function mapBusyToPhase(label: string, state?: string): ActivityPhase {
  const s = (state || "").toLowerCase();
  if (s === "waiting_input") {
    return "waiting";
  }
  const t = (label || "").toLowerCase();
  if (!t) {
    return "thinking";
  }
  if (t.includes("choosing model") || t.includes("escalat")) {
    return "choosing_model";
  }
  if (t.includes("planning") || t.includes("skill") || t.includes("explore")) {
    return "planning";
  }
  if (t.includes("appl") || t.includes("edit")) {
    return "editing";
  }
  if (t.includes("search") || t.includes("grep")) {
    return "searching";
  }
  if (t.includes("running") || t.includes("shell") || t.includes("command")) {
    return "running";
  }
  if (t.includes("mcp") || t.includes("tool")) {
    return "mcp";
  }
  if (t.includes("waiting")) {
    return "waiting";
  }
  if (t.includes("queued")) {
    return "queued";
  }
  return "thinking";
}

export class MainChatPanel {
  public static readonly viewType = "z.chatPanel";

  private panel: vscode.WebviewPanel | undefined;
  private messages: ChatMessage[] = [];
  private busyLabel = "";
  private activity: ActivityStripState = emptyActivity();
  private activityClearTimer: ReturnType<typeof setTimeout> | null = null;
  private waiting: WaitingPrompt | null = null;
  private streamingAssistantId: string | null = null;
  private threadId = "default";
  private queue: QueueState = { queueLen: 0, items: [], preview: null };
  private disposed = false;
  private postTimer: ReturnType<typeof setTimeout> | null = null;
  private connectionBanner = "";

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly manager: AppServerManager
  ) {
    manager.onNotification((method, params) => {
      void this.onNotification(method, params as Record<string, unknown>);
    });
    manager.onDidChange(() => {
      const conn = this.manager.connectionState;
      if (conn === "connected") {
        this.connectionBanner = "";
      } else if (conn === "error" || conn === "disconnected") {
        this.connectionBanner =
          this.manager.errorMessage || "App-server disconnected — Reconnect from the status bar.";
      }
      this.postStateThrottled();
    });
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
    if (msg.type === "reconnect") {
      try {
        await vscode.commands.executeCommand("z.reconnectAppServer");
        this.connectionBanner = "";
        this.postState();
      } catch (err) {
        this.connectionBanner =
          err instanceof Error ? err.message : "Reconnect failed";
        this.postState();
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
      this.resetActivityImmediate();
      this.postState();
      return;
    }
    if (msg.type === "clear") {
      this.messages = [];
      this.streamingAssistantId = null;
      this.waiting = null;
      this.busyLabel = "";
      this.resetActivityImmediate();
      this.queue = { queueLen: 0, items: [], preview: null };
      this.postState();
    }
  }

  private resetActivityImmediate(): void {
    if (this.activityClearTimer) {
      clearTimeout(this.activityClearTimer);
      this.activityClearTimer = null;
    }
    this.activity = emptyActivity();
  }

  private markActivityLive(phase?: ActivityPhase): void {
    if (this.activityClearTimer) {
      clearTimeout(this.activityClearTimer);
      this.activityClearTimer = null;
    }
    this.activity.live = true;
    if (phase) {
      this.activity.phase = phase;
    }
  }

  private settleActivitySoon(): void {
    this.activity.live = false;
    this.activity.phase = "idle";
    if (this.activityClearTimer) {
      clearTimeout(this.activityClearTimer);
    }
    this.activityClearTimer = setTimeout(() => {
      this.activityClearTimer = null;
      this.activity = emptyActivity();
      this.postState();
    }, 1500);
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
      this.activity = emptyActivity();
      this.markActivityLive("thinking");
      this.queue = { queueLen: 0, items: [], preview: null };
      this.postState();
    } catch (err) {
      if (!agentBusy) {
        this.busyLabel = "";
        this.resetActivityImmediate();
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
        if (this.activity.live) {
          this.settleActivitySoon();
        }
      } else if (state === "waiting_input") {
        this.busyLabel = label || "Waiting for your reply…";
        this.markActivityLive("waiting");
      } else {
        this.busyLabel = label || "Working…";
        this.markActivityLive(mapBusyToPhase(label, state));
        if (this.queue.queueLen > 0) {
          this.activity.phase = "queued";
        }
      }
      if (typeof params.queueLen === "number" && params.queueLen === 0) {
        this.queue = { queueLen: 0, items: [], preview: null };
      }
      this.postState();
      return;
    }
    if (method === "turn/activity") {
      this.markActivityLive();
      const n = (k: string) =>
        typeof params[k] === "number" && Number.isFinite(params[k] as number)
          ? Math.max(0, Math.floor(params[k] as number))
          : undefined;
      const editingFiles = n("editingFiles");
      const exploredFiles = n("exploredFiles");
      const searches = n("searches");
      const commands = n("commands");
      const mcpCalls = n("mcpCalls");
      const linesAdded = n("linesAdded");
      const linesRemoved = n("linesRemoved");
      if (editingFiles != null) {
        this.activity.editingFiles = editingFiles;
      }
      if (exploredFiles != null) {
        this.activity.exploredFiles = exploredFiles;
      }
      if (searches != null) {
        this.activity.searches = searches;
      }
      if (commands != null) {
        this.activity.commands = commands;
      }
      if (mcpCalls != null) {
        this.activity.mcpCalls = mcpCalls;
      }
      if (linesAdded != null) {
        this.activity.linesAdded = linesAdded;
      }
      if (linesRemoved != null) {
        this.activity.linesRemoved = linesRemoved;
      }
      if (params.modelId != null) {
        this.activity.modelId = String(params.modelId || "") || null;
      }
      if (params.phase != null && String(params.phase)) {
        const p = String(params.phase) as ActivityPhase;
        if (p !== "idle") {
          this.activity.phase = p;
        }
      }
      if (Array.isArray(params.fileNames)) {
        this.activity.fileNames = params.fileNames.map((x) => String(x));
      }
      this.postStateThrottled();
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
        this.activity = emptyActivity();
        this.markActivityLive("thinking");
      } else {
        this.activity = emptyActivity();
        this.markActivityLive("thinking");
      }
      this.postStateThrottled();
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
      this.postStateThrottled();
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
      this.markActivityLive(
        this.waiting.kind === "plan_confirm" ? "planning" : "waiting"
      );
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
      this.settleActivitySoon();
      this.postState();
      return;
    }
    if (method === "turn/error") {
      this.messages.push({
        id: `err-${Date.now()}`,
        role: "system",
        kind: "error",
        text: friendlyError(String(params.message || "turn failed")),
      });
      this.busyLabel = "";
      this.settleActivitySoon();
      this.postState();
      return;
    }
    if (method === "mcp/tool_started") {
      const server = String(params.serverName || "");
      const tool = String(params.toolName || "");
      this.messages.push({
        id: `tool-${params.callId || Date.now()}`,
        role: "system",
        kind: "tool",
        text: `▸ ${server}.${tool}…`,
      });
      this.markActivityLive("mcp");
      this.postStateThrottled();
      return;
    }
    if (method === "mcp/tool_finished") {
      const server = String(params.serverName || "");
      const tool = String(params.toolName || "");
      const ms = params.durationMs != null ? ` · ${params.durationMs}ms` : "";
      const id = `tool-${params.callId || Date.now()}`;
      const existing = this.messages.find((m) => m.id === id);
      const line = `✓ ${server}.${tool}${ms}`;
      if (existing) {
        existing.text = line;
      } else {
        this.messages.push({ id, role: "system", kind: "tool", text: line });
      }
      this.postStateThrottled();
      return;
    }
    if (method === "mcp/tool_error") {
      const server = String(params.serverName || "");
      const tool = String(params.toolName || "");
      this.messages.push({
        id: `tool-err-${Date.now()}`,
        role: "system",
        kind: "error",
        text: `✗ ${server}.${tool}: ${String(params.error || "failed")}`,
      });
      this.postState();
    }
  }

  private postStateThrottled(): void {
    if (this.postTimer) {
      return;
    }
    this.postTimer = setTimeout(() => {
      this.postTimer = null;
      this.postState();
    }, 50);
  }

  private postState(): void {
    if (!this.panel) {
      return;
    }
    this.panel.webview.postMessage({
      type: "state",
      connection: this.manager.connectionState,
      busyLabel: this.busyLabel,
      activity: this.activity,
      waiting: this.waiting,
      messages: this.messages,
      queue: this.queue,
      banner: this.connectionBanner,
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
      radial-gradient(ellipse 90% 55% at 50% -12%, rgba(212,137,74,0.12), transparent 58%),
      var(--z-bg);
  }
  #brand {
    padding: 22px 22px 8px;
    font-size: 30px; font-weight: 700; letter-spacing: -0.03em;
    color: var(--z-accent-bright);
  }
  #activity {
    padding: 0 20px 10px;
    min-height: 2.6em;
    font-size: 12px;
    color: var(--z-strip-fg);
  }
  #activity .line1 {
    color: var(--z-strip-fg);
    line-height: 1.35;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0 10px;
  }
  #activity .line1 .summary { flex: 1 1 auto; min-width: 12em; }
  #activity .line1 .verb { color: var(--z-strip-verb); font-weight: 600; }
  #activity .deltas {
    display: inline-flex;
    gap: 8px;
    font-variant-numeric: tabular-nums;
    font-size: 11px;
    white-space: nowrap;
  }
  #activity .delta-add { color: var(--z-delta-add); }
  #activity .delta-del { color: var(--z-delta-del); }
  #activity .line2 {
    color: var(--z-strip-phase);
    font-weight: 500;
    margin-top: 2px;
    line-height: 1.35;
  }
  #activity.busy .line2 {
    animation: z-phase-pulse 1.2s ease-in-out infinite;
  }
  @media (prefers-reduced-motion: reduce) {
    #activity.busy .line2 { animation: none; }
  }
  @keyframes z-phase-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }
  #activity.idle { color: var(--z-muted); }
  #activity.idle .line2 { display: none; }
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
    display: none; margin: 0 18px; padding: 14px 16px;
    border: 1px solid var(--z-border);
    background: var(--z-raised);
    border-radius: var(--z-radius);
  }
  #waiting.show { display: block; }
  #waiting .q { font-weight: 600; margin-bottom: 6px; color: var(--z-accent-bright); }
  #waiting .subject {
    max-height: 160px; overflow: auto; font-size: 12px; color: var(--z-muted);
    margin-bottom: 8px; white-space: pre-wrap;
  }
  #waiting .actions { display: flex; flex-wrap: wrap; gap: 8px; }
  #queue {
    display: none; margin: 10px 18px 0; padding: 12px 14px;
    border: 1px solid var(--z-border);
    background: var(--z-surface);
    font-size: 12px;
    border-radius: var(--z-radius);
  }
  #queue.show { display: block; }
  #queue .label {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--z-accent); margin-bottom: 4px;
  }
  #queue .preview { white-space: pre-wrap; word-break: break-word; color: var(--z-text); }
  #queue .more { color: var(--z-muted); margin-top: 4px; font-size: 11px; }
  #composer {
    position: sticky; bottom: 0;
    border-top: 1px solid var(--z-border);
    padding: 14px 18px 18px; display: flex; flex-direction: column; gap: 10px;
    background: linear-gradient(180deg, transparent, var(--z-surface) 18%);
  }
  #composer .composer-shell {
    border: 1px solid var(--z-border);
    background: var(--z-raised);
    border-radius: var(--z-radius);
    padding: 10px 12px 12px;
  }
  #banner {
    display: none; margin: 0 18px 8px; padding: 10px 12px;
    border: 1px solid var(--z-border); color: var(--z-accent-bright);
    font-size: 12px; background: var(--z-raised);
    border-radius: var(--z-radius-sm);
  }
  #banner.show { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  textarea {
    width: 100%; min-height: 72px; resize: vertical; box-sizing: border-box;
    padding: 10px 4px; font-family: inherit; font-size: 14px;
    border: none; background: transparent; box-shadow: none;
  }
  textarea:focus { outline: none; border: none; box-shadow: none; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .hint { font-size: 11px; color: var(--z-muted); }
  .msg.tool .bubble { color: var(--z-accent); font-size: 12px; }
  .msg.error .bubble { color: var(--z-accent-bright); font-size: 12px; }
  #waiting.kind-mcp_tool .q { color: var(--z-accent); }
  #waiting.kind-plan_confirm .q { color: var(--z-accent-bright); }
</style>
</head>
<body>
  <div id="app">
    <div id="brand">Z</div>
    <div id="activity" class="idle" aria-live="polite">
      <div class="line1"><span class="summary">Agent ready — you prompt, Z programs</span></div>
      <div class="line2"></div>
    </div>
    <div id="banner"><span id="bannerText"></span><button class="secondary" id="reconnect">Reconnect</button></div>
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
      <div class="composer-shell">
        <textarea id="input" placeholder="Prompt the agent…"></textarea>
        <div class="row">
          <button id="send">Send</button>
          <button class="secondary" id="cancel">Stop</button>
          <button class="secondary" id="clear">Clear</button>
          <span class="hint">Enter to send · Shift+Enter newline · busy → queues</span>
        </div>
      </div>
    </div>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    const msgsEl = document.getElementById('msgs');
    const activityEl = document.getElementById('activity');
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

    function plural(n, one, many) {
      return n === 1 ? one : many;
    }

    function shortModel(id) {
      if (!id) return '';
      const s = String(id);
      const slash = s.lastIndexOf('/');
      return slash >= 0 ? s.slice(slash + 1) : s;
    }

    const PHASE_COPY = {
      thinking: 'Thinking',
      planning: 'Planning next moves',
      editing: 'Applying edits',
      searching: 'Searching',
      running: 'Running commands',
      mcp: 'Using tools',
      choosing_model: 'Choosing model',
      waiting: 'Waiting for you',
      queued: 'Queued follow-up',
      idle: ''
    };

    function buildSummary(act) {
      const parts = [];
      if (act.editingFiles > 0) {
        parts.push('<span class="verb">Editing</span> ' + act.editingFiles + ' ' + plural(act.editingFiles, 'file', 'files'));
      }
      if (act.exploredFiles > 0) {
        parts.push('explored ' + act.exploredFiles + ' ' + plural(act.exploredFiles, 'file', 'files'));
      }
      if (act.searches > 0) {
        parts.push(act.searches + ' ' + plural(act.searches, 'search', 'searches'));
      }
      if (act.commands > 0) {
        parts.push('ran ' + act.commands + ' ' + plural(act.commands, 'command', 'commands'));
      }
      if (act.mcpCalls > 0) {
        parts.push(act.mcpCalls + ' MCP ' + plural(act.mcpCalls, 'tool', 'tools'));
      }
      if (act.phase === 'choosing_model') {
        parts.push('<span class="verb">Choosing model</span>');
      } else if (act.modelId) {
        parts.push('Using ' + escapeHtml(shortModel(act.modelId)));
      }
      // Drop least-important tokens if too long (explored → searches → commands).
      let joined = parts.join(', ');
      const dropOrder = ['explored ', ' search', 'ran ', ' MCP '];
      let guard = 0;
      while (joined.replace(/<[^>]+>/g, '').length > 90 && parts.length > 1 && guard < 8) {
        let dropped = false;
        for (const key of dropOrder) {
          const idx = parts.findIndex(p => p.replace(/<[^>]+>/g, '').includes(key.trim()) || p.includes(key));
          if (idx >= 0 && !(parts[idx].includes('Editing'))) {
            parts.splice(idx, 1);
            dropped = true;
            break;
          }
        }
        if (!dropped) break;
        joined = parts.join(', ');
        guard++;
      }
      return joined;
    }

    function renderActivity(act, conn, busyLabel) {
      const live = act && act.live;
      const phase = (act && act.phase) || 'idle';
      if (!live && phase === 'idle') {
        activityEl.className = 'idle';
        activityEl.innerHTML =
          '<div class="line1"><span class="summary">' +
          (conn === 'connected'
            ? 'Agent ready — you prompt, Z programs'
            : ('Z · ' + escapeHtml(conn || 'disconnected'))) +
          '</span></div><div class="line2"></div>';
        return;
      }
      activityEl.className = 'busy';
      const summary = buildSummary(act || {});
      let line1 = summary;
      if (!line1 && busyLabel) {
        line1 = escapeHtml(busyLabel);
      }
      if (!line1 && act && act.modelId) {
        line1 = 'Using ' + escapeHtml(shortModel(act.modelId));
      }
      const add = (act && act.linesAdded) || 0;
      const del = (act && act.linesRemoved) || 0;
      let deltas = '';
      if (add > 0 || del > 0) {
        deltas = '<span class="deltas">';
        if (add > 0) deltas += '<span class="delta-add">+' + add + '</span>';
        if (del > 0) deltas += '<span class="delta-del">−' + del + '</span>';
        deltas += '</span>';
      }
      const phaseCopy = PHASE_COPY[phase] || (busyLabel ? escapeHtml(busyLabel) : 'Thinking');
      const a11yDeltas =
        (add > 0 || del > 0)
          ? (' plus ' + add + ', minus ' + del)
          : '';
      activityEl.innerHTML =
        '<div class="line1">' +
          '<span class="summary">' + (line1 || '') + '</span>' +
          deltas +
        '</div>' +
        '<div class="line2">' + phaseCopy + '</div>';
      activityEl.setAttribute('aria-label', (line1.replace(/<[^>]+>/g, '') || phaseCopy) + a11yDeltas);
    }

    function renderMessages(messages) {
      msgsEl.innerHTML = messages.map(m => {
        const kind = m.kind || '';
        const role = kind === 'tool' ? 'Tool'
          : m.role === 'user' ? 'You'
          : m.role === 'assistant' ? 'Z'
          : kind === 'error' ? 'Error' : 'System';
        const cls = 'msg ' + m.role + (kind ? ' ' + kind : '');
        return '<div class="' + cls + '"><div class="role">' + role + '</div><div class="bubble">' + escapeHtml(m.text) + '</div></div>';
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
      waitingEl.className = 'kind-' + ((w && w.kind) || 'confirm');
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
      renderActivity(data.activity || { live: false, phase: 'idle' }, conn, busy);
      const banner = document.getElementById('banner');
      const bannerText = document.getElementById('bannerText');
      if (data.banner) {
        banner.classList.add('show');
        bannerText.textContent = data.banner;
      } else {
        banner.classList.remove('show');
      }
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
    document.getElementById('reconnect').onclick = () => vscode.postMessage({ type: 'reconnect' });
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

function friendlyError(raw: string): string {
  const s = (raw || "").toLowerCase();
  if (s.includes("401") || s.includes("unauth") || s.includes("sign in")) {
    return "Auth error — sign in from Profile, then retry.";
  }
  if (s.includes("429") || s.includes("rate")) {
    return "Gateway rate limited — wait a moment and retry.";
  }
  if (s.includes("mcp")) {
    return `MCP error: ${raw}`;
  }
  if (s.includes("econn") || s.includes("network") || s.includes("timed out")) {
    return "Network error — check connection and Reconnect.";
  }
  return raw.length > 400 ? `${raw.slice(0, 399)}…` : raw;
}
