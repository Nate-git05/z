/**
 * Minimal JSON-RPC client for z-app-server (IPC v0).
 * Transport: WebSocket. Mirrors Codex app-server handshake shape.
 */

import WebSocket from "ws";

export type JsonRpcId = string | number;

export interface InitializeResult {
  serverInfo: { name: string; version: string };
  zHome: string;
  capabilities: string[];
  workspaceRoot?: string | null;
}

type Pending = {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
};

export class AppServerClient {
  private ws: WebSocket | null = null;
  private nextId = 1;
  private pending = new Map<JsonRpcId, Pending>();
  private url: string;

  constructor(url: string) {
    this.url = url;
  }

  get connected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  async connect(): Promise<void> {
    if (this.connected) {
      return;
    }
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(this.url);
      const timer = setTimeout(() => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
        reject(new Error(`Timed out connecting to ${this.url}`));
      }, 5000);
      ws.on("open", () => {
        clearTimeout(timer);
        this.ws = ws;
        ws.on("message", (data) => this.onMessage(String(data)));
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
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
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
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }
    this.ws.send(JSON.stringify({ method, params: params ?? {} }));
  }

  async initialize(workspaceRoot?: string): Promise<InitializeResult> {
    const result = (await this.request("initialize", {
      clientInfo: { name: "z-editor", version: "0.1.0" },
      workspaceRoot,
    })) as InitializeResult;
    this.notify("initialized");
    return result;
  }

  private onMessage(raw: string): void {
    let msg: { id?: JsonRpcId; result?: unknown; error?: { message?: string } };
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }
    if (msg.id === undefined || msg.id === null) {
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
