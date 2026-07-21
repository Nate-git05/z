/**
 * Phase 3b — spawn / attach / stop z-app-server from the Z contribution.
 */

import * as vscode from "vscode";
import { spawn, ChildProcess, execFileSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";
import {
  AppServerClient,
  parseHostPort,
  probeAppServer,
  AuthStatus,
  InitializeResult,
} from "./appServerClient";

export type ConnectionState =
  | "disconnected"
  | "starting"
  | "connecting"
  | "connected"
  | "error";

export class AppServerManager implements vscode.Disposable {
  private client: AppServerClient | null = null;
  private proc: ChildProcess | null = null;
  private spawnedByUs = false;
  private state: ConnectionState = "disconnected";
  private lastError: string | null = null;
  private initResult: InitializeResult | null = null;
  private readonly output: vscode.OutputChannel;
  private readonly onChangeEmitter = new vscode.EventEmitter<void>();
  readonly onDidChange = this.onChangeEmitter.event;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(private readonly context: vscode.ExtensionContext) {
    this.output = vscode.window.createOutputChannel("Z App Server");
    this.disposables.push(this.output, this.onChangeEmitter);
  }

  get connectionState(): ConnectionState {
    return this.state;
  }

  get errorMessage(): string | null {
    return this.lastError;
  }

  get rpc(): AppServerClient | null {
    return this.client?.connected ? this.client : null;
  }

  get serverInfo(): InitializeResult | null {
    return this.initResult;
  }

  appServerUrl(): string {
    return (
      process.env.Z_APP_SERVER_URL ||
      vscode.workspace.getConfiguration("z").get<string>("appServerUrl") ||
      "ws://127.0.0.1:8741"
    );
  }

  private pidFilePath(): string {
    const { port } = parseHostPort(this.appServerUrl());
    return path.join(os.homedir(), ".z", "app-server", `${port}.pid`);
  }

  private setState(state: ConnectionState, err?: string | null): void {
    this.state = state;
    if (err !== undefined) {
      this.lastError = err;
    }
    this.onChangeEmitter.fire();
  }

  /** Ensure a server is listening, then connect + initialize. */
  async ensureConnected(): Promise<void> {
    const url = this.appServerUrl();
    const reachable = await probeAppServer(url);
    if (!reachable) {
      const auto = vscode.workspace
        .getConfiguration("z")
        .get<boolean>("autoStartAppServer", true);
      if (!auto) {
        throw new Error(
          `z-app-server not reachable at ${url} (auto-start disabled)`
        );
      }
      await this.startProcess();
      await this.waitUntilReachable(url, 15000);
    }
    await this.connectAndInitialize();
  }

  async startProcess(): Promise<void> {
    if (this.proc && !this.proc.killed) {
      this.output.appendLine("app-server process already tracked");
      return;
    }
    if (await probeAppServer(this.appServerUrl())) {
      this.output.appendLine("app-server already reachable — attaching");
      this.spawnedByUs = false;
      return;
    }

    this.setState("starting");
    const url = this.appServerUrl();
    const { host, port } = parseHostPort(url);
    const pidFile = this.pidFilePath();
    fs.mkdirSync(path.dirname(pidFile), { recursive: true });

    const zBin = this.resolveZBinary();
    const args = [
      "app-server",
      "--host",
      host,
      "--port",
      String(port),
      "--pid-file",
      pidFile,
    ];
    this.output.appendLine(`spawning: ${zBin} ${args.join(" ")}`);

    const child = spawn(zBin, args, {
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env },
      detached: false,
    });
    this.proc = child;
    this.spawnedByUs = true;

    child.stdout?.on("data", (buf) => this.output.append(String(buf)));
    child.stderr?.on("data", (buf) => this.output.append(String(buf)));
    child.on("exit", (code, signal) => {
      this.output.appendLine(`app-server exited code=${code} signal=${signal}`);
      this.proc = null;
      if (this.state === "connected" || this.state === "connecting") {
        this.setState("disconnected", "app-server process exited");
        this.client?.disconnect();
        this.client = null;
      }
    });
    child.on("error", (err) => {
      this.output.appendLine(`spawn error: ${err.message}`);
      this.setState("error", err.message);
    });
  }

  private resolveZBinary(): string {
    const configured = vscode.workspace
      .getConfiguration("z")
      .get<string>("zBinary");
    if (configured && configured.trim()) {
      return configured.trim();
    }
    try {
      const which = process.platform === "win32" ? "where" : "which";
      const out = execFileSync(which, ["z"], { encoding: "utf8" }).trim();
      const first = out.split(/\r?\n/)[0];
      if (first) {
        return first;
      }
    } catch {
      /* fall through */
    }
    return "z";
  }

  private async waitUntilReachable(url: string, timeoutMs: number): Promise<void> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await probeAppServer(url, 400)) {
        return;
      }
      await sleep(250);
    }
    throw new Error(`Timed out waiting for z-app-server at ${url}`);
  }

  async connectAndInitialize(): Promise<InitializeResult> {
    this.setState("connecting");
    this.client?.disconnect();
    this.client = new AppServerClient(this.appServerUrl());
    try {
      await this.client.connect();
      // Warm health (allowed pre-initialize)
      try {
        await this.client.health();
      } catch {
        /* older servers may not have server/health until after init — ok */
      }
      const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      this.initResult = await this.client.initialize(root);
      this.setState("connected", null);
      this.output.appendLine(
        `connected ${this.initResult.serverInfo.name} ${this.initResult.serverInfo.version}`
      );
      return this.initResult;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.setState("error", msg);
      this.client?.disconnect();
      this.client = null;
      throw err;
    }
  }

  async openWorkspace(root: string): Promise<void> {
    const rpc = this.rpc;
    if (!rpc) {
      throw new Error("Not connected");
    }
    await rpc.request("workspace/open", { root });
    this.output.appendLine(`workspace/open ${root}`);
  }

  async authStatus(): Promise<AuthStatus> {
    const rpc = this.rpc;
    if (!rpc) {
      throw new Error("Not connected");
    }
    return (await rpc.request("auth/status")) as AuthStatus;
  }

  async stop(): Promise<void> {
    this.client?.disconnect();
    this.client = null;
    this.initResult = null;
    if (this.proc && !this.proc.killed && this.spawnedByUs) {
      this.output.appendLine("stopping spawned app-server");
      this.proc.kill();
      this.proc = null;
    }
    this.spawnedByUs = false;
    this.setState("disconnected", null);
  }

  dispose(): void {
    void this.stop();
    for (const d of this.disposables) {
      d.dispose();
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
