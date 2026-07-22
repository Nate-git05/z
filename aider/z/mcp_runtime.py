"""
MCP SessionManager — stdio JSON-RPC runtime for Z Editor (Phase 11).

Spawns local MCP servers from ``~/.z/mcp`` connections, speaks a minimal
subset of the MCP protocol (initialize → tools/list → tools/call), and
exposes probe/list/call for turn wiring and ``mcp/test``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# Secret key → process env for known servers
_ENV_MAP: dict[str, dict[str, str]] = {
    "github": {
        "token": "GITHUB_PERSONAL_ACCESS_TOKEN",
        "access_token": "GITHUB_PERSONAL_ACCESS_TOKEN",
    },
    "brave-search": {"api_key": "BRAVE_API_KEY"},
    "slack": {"bot_token": "SLACK_BOT_TOKEN"},
    "postgres": {"database_url": "POSTGRES_CONNECTION_STRING"},
}


def runtime_enabled() -> bool:
    raw = os.environ.get("Z_MCP_RUNTIME", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass
class ToolDesc:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class CallResult:
    ok: bool
    text: str = ""
    error: Optional[str] = None
    duration_ms: int = 0
    call_id: str = ""


class McpSession:
    """One stdio MCP server process + JSON-RPC framing (Content-Length)."""

    def __init__(
        self,
        connection_id: str,
        server_name: str,
        command: list[str],
        env: dict[str, str],
        *,
        cwd: Optional[str] = None,
    ):
        self.connection_id = connection_id
        self.server_name = server_name
        self.command = command
        self.env = env
        self.cwd = cwd
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._initialized = False
        self._tools: list[ToolDesc] = []

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, *, timeout: float = 20.0) -> None:
        if self.alive and self._initialized:
            return
        self.close()
        full_env = os.environ.copy()
        full_env.update(self.env)
        # Avoid leaking interactive prompts
        full_env.setdefault("CI", "1")
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            cwd=self.cwd,
            bufsize=0,
        )
        self._initialized = False
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "z-editor", "version": "0.7.0"},
            },
            timeout=timeout,
        )
        self._notify("notifications/initialized", {})
        self._initialized = True
        self.refresh_tools(timeout=timeout)

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        self._initialized = False
        self._tools = []
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass

    def refresh_tools(self, *, timeout: float = 15.0) -> list[ToolDesc]:
        result = self._rpc("tools/list", {}, timeout=timeout)
        tools_raw = (result or {}).get("tools") or []
        out: list[ToolDesc] = []
        for t in tools_raw:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            out.append(
                ToolDesc(
                    name=name,
                    description=str(t.get("description") or ""),
                    input_schema=dict(t.get("inputSchema") or t.get("input_schema") or {}),
                )
            )
        self._tools = out
        return list(out)

    def list_tools(self) -> list[ToolDesc]:
        return list(self._tools)

    def call_tool(
        self,
        name: str,
        arguments: Optional[dict[str, Any]] = None,
        *,
        timeout: float = 60.0,
    ) -> CallResult:
        call_id = str(uuid.uuid4())
        t0 = time.perf_counter()
        try:
            result = self._rpc(
                "tools/call",
                {"name": name, "arguments": arguments or {}},
                timeout=timeout,
            )
            text = _result_to_text(result)
            return CallResult(
                ok=True,
                text=text,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                call_id=call_id,
            )
        except Exception as err:
            return CallResult(
                ok=False,
                error=str(err),
                duration_ms=int((time.perf_counter() - t0) * 1000),
                call_id=call_id,
            )

    def _notify(self, method: str, params: dict) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._write(msg)

    def _rpc(self, method: str, params: dict, *, timeout: float) -> Any:
        with self._lock:
            if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
                raise RuntimeError("MCP session not started")
            req_id = self._next_id
            self._next_id += 1
            self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            deadline = time.time() + timeout
            while time.time() < deadline:
                msg = self._read_message(timeout=max(0.1, deadline - time.time()))
                if msg is None:
                    continue
                if msg.get("id") == req_id:
                    if "error" in msg:
                        err = msg["error"]
                        raise RuntimeError(
                            f"MCP error: {err.get('message') or err}"
                            if isinstance(err, dict)
                            else f"MCP error: {err}"
                        )
                    return msg.get("result")
                # Ignore notifications / unmatched
            raise TimeoutError(f"MCP RPC timeout for {method}")

    def _write(self, msg: dict) -> None:
        assert self._proc and self._proc.stdin
        body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + body)
        self._proc.stdin.flush()

    def _read_message(self, *, timeout: float) -> Optional[dict]:
        assert self._proc and self._proc.stdout
        stdout = self._proc.stdout
        # Content-Length framing
        headers: dict[str, str] = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = _readline_timeout(stdout, max(0.05, deadline - time.time()))
            if line is None:
                if self._proc.poll() is not None:
                    err = ""
                    try:
                        if self._proc.stderr:
                            err = self._proc.stderr.read().decode("utf-8", errors="replace")[:500]
                    except Exception:
                        pass
                    raise RuntimeError(f"MCP process exited: {err or self._proc.returncode}")
                continue
            if line in (b"\r\n", b"\n", b""):
                if headers:
                    break
                continue
            try:
                text = line.decode("ascii", errors="replace").strip()
            except Exception:
                continue
            if ":" in text:
                k, v = text.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        if not headers:
            return None
        length = int(headers.get("content-length") or 0)
        if length <= 0:
            return None
        body = _readexact_timeout(stdout, length, max(0.05, deadline - time.time()))
        if body is None:
            return None
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as err:
            raise RuntimeError(f"Invalid MCP JSON: {err}") from err
        return data if isinstance(data, dict) else None


def _readline_timeout(stream, timeout: float) -> Optional[bytes]:
    """Best-effort readline with timeout using a worker (stdio may block)."""
    result: list[Optional[bytes]] = [None]
    exc: list[Optional[BaseException]] = [None]

    def _run():
        try:
            result[0] = stream.readline()
        except BaseException as e:  # noqa: BLE001
            exc[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    if exc[0]:
        raise exc[0]
    return result[0]


def _readexact_timeout(stream, n: int, timeout: float) -> Optional[bytes]:
    result: list[Optional[bytes]] = [None]
    exc: list[Optional[BaseException]] = [None]

    def _run():
        try:
            buf = b""
            while len(buf) < n:
                chunk = stream.read(n - len(buf))
                if not chunk:
                    break
                buf += chunk
            result[0] = buf if len(buf) == n else None
        except BaseException as e:  # noqa: BLE001
            exc[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    if exc[0]:
        raise exc[0]
    return result[0]


def _result_to_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    else:
                        parts.append(json.dumps(item, indent=2))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return json.dumps(result, indent=2)
    return str(result)


class SessionManager:
    """Process-wide MCP sessions keyed by connection id."""

    def __init__(self) -> None:
        self._sessions: dict[str, McpSession] = {}
        self._lock = threading.Lock()

    def ensure_session(self, connection_id: str, *, timeout: float = 20.0) -> McpSession:
        if not runtime_enabled():
            raise RuntimeError("MCP runtime disabled (Z_MCP_RUNTIME=0)")
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing and existing.alive and existing._initialized:
                return existing
        session = self._build_session(connection_id)
        session.start(timeout=timeout)
        with self._lock:
            old = self._sessions.get(connection_id)
            if old and old is not session:
                old.close()
            self._sessions[connection_id] = session
        return session

    def list_tools(self, connection_id: str) -> list[ToolDesc]:
        return self.ensure_session(connection_id).list_tools()

    def call_tool(
        self,
        connection_id: str,
        name: str,
        arguments: Optional[dict[str, Any]] = None,
        *,
        timeout: float = 60.0,
    ) -> CallResult:
        session = self.ensure_session(connection_id)
        return session.call_tool(name, arguments, timeout=timeout)

    def drop_session(self, connection_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(connection_id, None)
        if session:
            session.close()

    def drop_all(self) -> None:
        with self._lock:
            ids = list(self._sessions.keys())
        for cid in ids:
            self.drop_session(cid)

    def probe(self, connection_id: str, *, timeout: float = 20.0) -> dict[str, Any]:
        if not runtime_enabled():
            return {"ok": False, "error": "MCP runtime disabled (Z_MCP_RUNTIME=0)"}
        try:
            session = self.ensure_session(connection_id, timeout=timeout)
            tools = session.list_tools()
            try:
                from aider.z import mcp_local

                mcp_local.set_cached_tools(connection_id, [t.name for t in tools])
            except Exception:
                pass
            return {
                "ok": True,
                "mode": "mcp-handshake",
                "tools_count": len(tools),
                "tools": [t.name for t in tools[:40]],
                "serverName": session.server_name,
            }
        except Exception as err:
            return {"ok": False, "error": str(err), "mode": "mcp-handshake"}

    def index_all_tools(self, *, spawn: bool = False) -> list[dict[str, Any]]:
        """Build catalog for prompt injection: server + tool names.

        Default uses cached tool names (no spawn). Set spawn=True to handshake
        every connection (slow; used by explicit refresh).
        """
        from aider.z import mcp_local

        if not runtime_enabled():
            return []
        if not spawn:
            return mcp_local.tool_index_from_cache()
        out: list[dict[str, Any]] = []
        for conn in mcp_local.list_connections(enabled_only=True):
            cid = str(conn.get("id") or "")
            server = str(conn.get("serverName") or conn.get("server_name") or "")
            if not cid or conn.get("status") == "error":
                continue
            try:
                tools = self.list_tools(cid)
                mcp_local.set_cached_tools(cid, [t.name for t in tools])
            except Exception:
                tools = []
            for t in tools:
                out.append(
                    {
                        "connectionId": cid,
                        "serverName": server,
                        "toolName": t.name,
                        "description": (t.description or "")[:200],
                    }
                )
        return out or mcp_local.tool_index_from_cache()

    def resolve_tool(
        self, server_name: str, tool_name: str
    ) -> Optional[tuple[str, str]]:
        """Return (connection_id, server_name) for a server/tool pair."""
        from aider.z import mcp_local

        server_name = (server_name or "").strip()
        tool_name = (tool_name or "").strip()
        for conn in mcp_local.list_connections(enabled_only=True):
            name = str(conn.get("serverName") or "")
            if server_name and name != server_name and not name.startswith(server_name):
                continue
            cid = str(conn.get("id") or "")
            if not cid:
                continue
            try:
                tools = self.list_tools(cid)
            except Exception:
                continue
            if any(t.name == tool_name for t in tools):
                return cid, name
            if not server_name and any(t.name == tool_name for t in tools):
                return cid, name
        # If server matched but tools not listed yet, still allow call
        for conn in mcp_local.list_connections(enabled_only=True):
            name = str(conn.get("serverName") or "")
            if name == server_name or (
                server_name and name.startswith(f"{server_name}-")
            ):
                return str(conn["id"]), name
        return None

    def _build_session(self, connection_id: str) -> McpSession:
        from aider.z import mcp_local

        rows = mcp_local._load_connections()
        conn = next((c for c in rows if c.id == connection_id), None)
        if conn is None:
            raise ValueError(f"Connection not found: {connection_id}")
        secrets = mcp_local._load_secrets().get(connection_id) or {}
        command = _build_command(conn.config, conn.public_fields)
        if not command:
            raise ValueError(
                f"No stdio command for {conn.server_name} — set command/args in config"
            )
        env = _build_env(conn.server_name, secrets, conn.public_fields, conn.config)
        return McpSession(
            connection_id=connection_id,
            server_name=conn.server_name,
            command=command,
            env=env,
        )


_MANAGER: Optional[SessionManager] = None
_MANAGER_LOCK = threading.Lock()


def get_session_manager() -> SessionManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = SessionManager()
        return _MANAGER


def _build_command(config: dict, public_fields: dict) -> list[str]:
    cfg = dict(config or {})
    cmd = cfg.get("command") or public_fields.get("command")
    args = cfg.get("args")
    if isinstance(cmd, list):
        return [str(x) for x in cmd]
    if not cmd:
        return []
    parts = str(cmd).split()
    if isinstance(args, list):
        parts.extend(str(a) for a in args)
    elif isinstance(args, str) and args.strip():
        parts.extend(args.split())
    # Resolve npx/node via PATH
    if parts and not Path(parts[0]).exists() and not shutil.which(parts[0]):
        # still return — probe will fail clearly
        pass
    return parts


def _build_env(
    server_name: str,
    secrets: dict[str, str],
    public_fields: dict,
    config: dict,
) -> dict[str, str]:
    env: dict[str, str] = {}
    base = server_name.split("-")[0] if server_name.startswith("custom") else server_name
    # custom-* → no special map
    mapping = _ENV_MAP.get(server_name) or _ENV_MAP.get(base) or {}
    for key, value in {**(public_fields or {}), **(secrets or {})}.items():
        if value is None or value == "":
            continue
        env_key = mapping.get(key) or key.upper()
        env[str(env_key)] = str(value)
        # Also set common aliases
        if key in ("token", "access_token") and "GITHUB" not in env_key:
            env.setdefault("GITHUB_PERSONAL_ACCESS_TOKEN", str(value))
    # Pass through non-secret config strings as env when useful
    for key in ("allowed_dirs", "root_path"):
        if key in (config or {}):
            env[key.upper()] = str(config[key])
    return env
