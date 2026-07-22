/**
 * Minimal JSON-RPC client for z-app-server (IPC v0).
 * Transport: WebSocket. Mirrors Codex app-server handshake shape.
 */

export type JsonRpcId = string | number;

/** Lazy-load `ws` so extension activate never fails on module import. */
function loadWs(): {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  WebSocket: new (url: string) => any;
  OPEN: number;
} {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const mod = require("ws");
  const WS = mod?.default ?? mod;
  const OPEN = (WS as { OPEN?: number }).OPEN ?? 1;
  return { WebSocket: WS as new (url: string) => unknown, OPEN };
}

export interface InitializeResult {
  serverInfo: { name: string; version: string };
  zHome: string;
  capabilities: string[];
  workspaceRoot?: string | null;
}

export interface AuthStatus {
  authenticated?: boolean;
  email?: string | null;
  name?: string | null;
  displayName?: string | null;
  auth_mode?: string | null;
  selected_model?: string | null;
  authBaseUrl?: string;
  login?: LoginStatus | null;
}

export interface LoginStatus {
  status: "idle" | "pending" | "succeeded" | "failed" | "cancelled" | string;
  method?: string | null;
  loginUrl?: string | null;
  state?: string | null;
  error?: string | null;
  email?: string | null;
}

type Pending = {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
};

type NotifyHandler = (method: string, params: unknown) => void;

export class AppServerClient {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private ws: any = null;
  private nextId = 1;
  private pending = new Map<JsonRpcId, Pending>();
  private url: string;
  private onNotify: NotifyHandler | null = null;
  private openState = 1;

  constructor(url: string) {
    this.url = url;
  }

  get connected(): boolean {
    return this.ws !== null && this.ws.readyState === this.openState;
  }

  get serverUrl(): string {
    return this.url;
  }

  setNotificationHandler(handler: NotifyHandler | null): void {
    this.onNotify = handler;
  }

  async connect(timeoutMs = 5000): Promise<void> {
    if (this.connected) {
      return;
    }
    const { WebSocket: WS, OPEN } = loadWs();
    this.openState = OPEN;
    await new Promise<void>((resolve, reject) => {
      const ws = new WS(this.url);
      const timer = setTimeout(() => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
        reject(new Error(`Timed out connecting to ${this.url}`));
      }, timeoutMs);
      ws.on("open", () => {
        clearTimeout(timer);
        this.ws = ws;
        ws.on("message", (data: unknown) => this.onMessage(String(data)));
        ws.on("close", () => {
          this.ws = null;
          for (const [, p] of this.pending) {
            p.reject(new Error("z-app-server disconnected"));
          }
          this.pending.clear();
        });
        resolve();
      });
      ws.on("error", () => {
        clearTimeout(timer);
        reject(new Error(`Failed to connect to ${this.url}`));
      });
    });
  }

  disconnect(): void {
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        /* ignore */
      }
      this.ws = null;
    }
  }

  async request(method: string, params?: Record<string, unknown>): Promise<unknown> {
    if (!this.ws || this.ws.readyState !== this.openState) {
      throw new Error("Not connected to z-app-server");
    }
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params: params ?? {} });
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws!.send(payload);
    });
  }

  notify(method: string, params?: Record<string, unknown>): void {
    if (!this.ws || this.ws.readyState !== this.openState) {
      return;
    }
    this.ws.send(JSON.stringify({ method, params: params ?? {} }));
  }

  async initialize(workspaceRoot?: string): Promise<InitializeResult> {
    const result = (await this.request("initialize", {
      clientInfo: { name: "z-editor", version: "0.3.0" },
      workspaceRoot,
    })) as InitializeResult;
    this.notify("initialized");
    return result;
  }

  async health(): Promise<{ ok: boolean; pid?: number; initialized?: boolean }> {
    return (await this.request("server/health")) as {
      ok: boolean;
      pid?: number;
      initialized?: boolean;
    };
  }

  private onMessage(raw: string): void {
    let msg: {
      id?: JsonRpcId;
      method?: string;
      params?: unknown;
      result?: unknown;
      error?: { message?: string };
    };
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }
    if (msg.id === undefined || msg.id === null) {
      if (msg.method && this.onNotify) {
        this.onNotify(msg.method, msg.params);
      }
      return;
    }
    const pending = this.pending.get(msg.id);
    if (!pending) {
      return;
    }
    this.pending.delete(msg.id);
    if (msg.error) {
      pending.reject(new Error(msg.error.message || "RPC error"));
    } else {
      pending.resolve(msg.result);
    }
  }
}

/** Probe whether something accepts WS connections at url (no initialize). */
export async function probeAppServer(url: string, timeoutMs = 800): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    let ws: { close: () => void; on: (ev: string, cb: () => void) => void };
    try {
      const { WebSocket: WS } = loadWs();
      ws = new WS(url);
    } catch {
      resolve(false);
      return;
    }
    const timer = setTimeout(() => {
      if (settled) {
        return;
      }
      settled = true;
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      resolve(false);
    }, timeoutMs);
    ws.on("open", () => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      resolve(true);
    });
    ws.on("error", () => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolve(false);
    });
  });
}

export function parseHostPort(url: string): { host: string; port: number } {
  try {
    const u = new URL(url.includes("://") ? url : `ws://${url}`);
    const host = u.hostname || "127.0.0.1";
    const port = u.port ? Number(u.port) : 8741;
    return { host, port };
  } catch {
    return { host: "127.0.0.1", port: 8741 };
  }
}
