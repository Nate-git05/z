"""
Local MCP connection store for Z Editor (Phase 10).

Persists connections under ``~/.z/mcp/`` so the desktop app can connect,
test, and list MCP servers without requiring the cloud DB for every
operation. Optional sync pushes to ``/v1/mcp/*`` when authenticated.

Secrets are stored in a separate file with restrictive permissions.
First-use confirmations (D9) are tracked per ``serverName::toolName``.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from aider.z.auth import get_auth_base_url
from aider.z.credentials import load_credentials


def mcp_root() -> Path:
    override = os.environ.get("Z_MCP_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".z" / "mcp"


def connections_path() -> Path:
    return mcp_root() / "connections.json"


def secrets_path() -> Path:
    return mcp_root() / "secrets.json"


def first_use_path() -> Path:
    return mcp_root() / "first_use.json"


# Catalog aligned with z_server MCP catalog (subset for V1).
# GitHub: OAuth primary (desktop deep-link) + PAT fallback via fields.
DEFAULT_CATALOG: list[dict[str, Any]] = [
    {
        "serverName": "github",
        "displayName": "GitHub",
        "connectionType": "oauth",
        "description": "GitHub issues, PRs, and repo tools. OAuth preferred; PAT also works.",
        "allowPatFallback": True,
        "oauthStartPath": "/v1/mcp/oauth/start?server_name=github",
        "fields": [
            {
                "key": "token",
                "label": "Personal access token (fallback)",
                "secret": True,
                "required": False,
            },
        ],
        "defaultConfig": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
    },
    {
        "serverName": "filesystem",
        "displayName": "Filesystem",
        "connectionType": "manual",
        "description": "Read/write files in allowed directories.",
        "fields": [
            {
                "key": "allowed_dirs",
                "label": "Allowed directories (comma-separated)",
                "secret": False,
                "required": True,
            },
        ],
        "defaultConfig": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        },
    },
    {
        "serverName": "brave-search",
        "displayName": "Brave Search",
        "connectionType": "manual",
        "description": "Web search via Brave API.",
        "fields": [
            {
                "key": "api_key",
                "label": "Brave Search API key",
                "secret": True,
                "required": True,
            },
        ],
        "defaultConfig": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        },
    },
    {
        "serverName": "custom",
        "displayName": "Custom MCP",
        "connectionType": "manual",
        "description": "stdio command or HTTP endpoint.",
        "fields": [
            {"key": "label", "label": "Display name", "secret": False, "required": True},
            {
                "key": "command",
                "label": "Command (stdio)",
                "secret": False,
                "required": False,
            },
            {
                "key": "url",
                "label": "URL (HTTP/SSE)",
                "secret": False,
                "required": False,
            },
            {
                "key": "token",
                "label": "Bearer token (optional)",
                "secret": True,
                "required": False,
            },
        ],
        "defaultConfig": {},
    },
    {
        "serverName": "linear",
        "displayName": "Linear",
        "connectionType": "oauth",
        "description": "Linear issues (OAuth via web).",
        "fields": [],
        "defaultConfig": {},
        "oauthStartPath": "/v1/mcp/oauth/start?server_name=linear",
    },
]


@dataclass
class McpConnectionLocal:
    id: str
    server_name: str
    display_name: str
    connection_type: str = "manual"
    enabled: bool = True
    status: str = "connected"
    config: dict[str, Any] = field(default_factory=dict)
    # Non-secret field values mirrored for UI (secrets live in secrets.json)
    public_fields: dict[str, str] = field(default_factory=dict)
    last_error: Optional[str] = None
    remote_id: Optional[str] = None
    cached_tools: list[str] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "serverName": self.server_name,
            "displayName": self.display_name,
            "connectionType": self.connection_type,
            "enabled": self.enabled,
            "status": self.status,
            "config": dict(self.config),
            "publicFields": dict(self.public_fields),
            "lastError": self.last_error,
            "remoteId": self.remote_id,
            "hasSecrets": _connection_has_secrets(self.id),
            "cachedTools": list(self.cached_tools),
        }


def _ensure_dir() -> None:
    root = mcp_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any, *, private: bool = False) -> None:
    _ensure_dir()
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if private:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _load_connections() -> list[McpConnectionLocal]:
    raw = _read_json(connections_path(), {"connections": []})
    rows = raw.get("connections") if isinstance(raw, dict) else []
    out: list[McpConnectionLocal] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            McpConnectionLocal(
                id=str(row.get("id") or uuid4()),
                server_name=str(row.get("server_name") or row.get("serverName") or ""),
                display_name=str(row.get("display_name") or row.get("displayName") or ""),
                connection_type=str(
                    row.get("connection_type") or row.get("connectionType") or "manual"
                ),
                enabled=bool(row.get("enabled", True)),
                status=str(row.get("status") or "connected"),
                config=dict(row.get("config") or {}),
                public_fields=dict(row.get("public_fields") or row.get("publicFields") or {}),
                last_error=row.get("last_error") or row.get("lastError"),
                remote_id=row.get("remote_id") or row.get("remoteId"),
                cached_tools=list(row.get("cached_tools") or row.get("cachedTools") or []),
            )
        )
    return out


def _save_connections(rows: list[McpConnectionLocal]) -> None:
    payload = {
        "connections": [
            {
                "id": c.id,
                "server_name": c.server_name,
                "display_name": c.display_name,
                "connection_type": c.connection_type,
                "enabled": c.enabled,
                "status": c.status,
                "config": c.config,
                "public_fields": c.public_fields,
                "last_error": c.last_error,
                "remote_id": c.remote_id,
                "cached_tools": list(c.cached_tools or []),
            }
            for c in rows
        ]
    }
    _write_json(connections_path(), payload)


def set_cached_tools(connection_id: str, tools: list[str]) -> None:
    rows = _load_connections()
    for c in rows:
        if c.id == connection_id:
            c.cached_tools = [str(t) for t in tools]
            _save_connections(rows)
            return


def tool_index_from_cache() -> list[dict[str, Any]]:
    """Prompt catalog without spawning processes."""
    out: list[dict[str, Any]] = []
    for c in _load_connections():
        if not c.enabled:
            continue
        tools = list(c.cached_tools or [])
        if not tools:
            out.append(
                {
                    "connectionId": c.id,
                    "serverName": c.server_name,
                    "toolName": "*",
                    "description": "connected — run mcp/test or first call to discover tools",
                }
            )
            continue
        for name in tools:
            out.append(
                {
                    "connectionId": c.id,
                    "serverName": c.server_name,
                    "toolName": name,
                    "description": "",
                }
            )
    return out


def _load_secrets() -> dict[str, dict[str, str]]:
    raw = _read_json(secrets_path(), {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for cid, secrets in raw.items():
        if isinstance(secrets, dict):
            out[str(cid)] = {str(k): str(v) for k, v in secrets.items()}
    return out


def _save_secrets(secrets: dict[str, dict[str, str]]) -> None:
    _write_json(secrets_path(), secrets, private=True)


def _connection_has_secrets(connection_id: str) -> bool:
    return bool(_load_secrets().get(connection_id))


def catalog() -> list[dict[str, Any]]:
    """Return catalog (local defaults; optionally merge remote later)."""
    return [dict(entry) for entry in DEFAULT_CATALOG]


def get_catalog_entry(server_name: str) -> Optional[dict[str, Any]]:
    name = (server_name or "").strip().lower()
    for entry in DEFAULT_CATALOG:
        if str(entry["serverName"]).lower() == name:
            return dict(entry)
    return None


def list_connections(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    rows = _load_connections()
    if enabled_only:
        rows = [c for c in rows if c.enabled]
    return [c.to_public_dict() for c in rows]


def connect(
    server_name: str,
    *,
    credentials: Optional[dict[str, str]] = None,
    config: Optional[dict[str, Any]] = None,
    display_name: Optional[str] = None,
    scope: str = "personal",
) -> dict[str, Any]:
    """Create or update a local MCP connection."""
    del scope  # reserved for cloud sync parity
    entry = get_catalog_entry(server_name)
    if entry is None:
        raise ValueError(f"Unknown MCP server '{server_name}'.")

    creds_in = {str(k): str(v) for k, v in (credentials or {}).items() if v is not None}
    if entry.get("connectionType") == "oauth" and not entry.get("allowPatFallback"):
        raise ValueError(
            "OAuth MCP servers must be connected via OAuth "
            f"(open {entry.get('oauthStartPath') or '/v1/mcp/oauth/start'})."
        )
    if (
        entry.get("connectionType") == "oauth"
        and entry.get("allowPatFallback")
        and not creds_in.get("token")
        and not creds_in.get("access_token")
    ):
        raise ValueError(
            "GitHub OAuth: use mcp/oauthStart, or pass a PAT in credentials.token."
        )

    cfg = {**(entry.get("defaultConfig") or {}), **(config or {})}
    secrets_payload: dict[str, str] = {}
    public_fields: dict[str, str] = {}

    for field_def in entry.get("fields") or []:
        if not isinstance(field_def, dict):
            continue
        key = str(field_def.get("key") or "")
        if not key:
            continue
        value = creds_in.get(key)
        if value is None and key in cfg:
            value = str(cfg.pop(key))
        if value is None or value == "":
            if field_def.get("required"):
                raise ValueError(f"Missing required field: {key}")
            continue
        if field_def.get("secret"):
            secrets_payload[key] = str(value)
        else:
            public_fields[key] = str(value)
            cfg[key] = value

    for key, value in creds_in.items():
        if key in secrets_payload or key in public_fields:
            continue
        secrets_payload[key] = value

    name = server_name.strip().lower()
    disp = display_name or entry.get("displayName") or name
    if name == "custom" and public_fields.get("label"):
        disp = public_fields["label"]
        slug = "".join(ch if ch.isalnum() else "-" for ch in disp.lower())[:40]
        name = f"custom-{slug}-{uuid4().hex[:4]}"

    # PAT fallback on an oauth catalog entry → store as manual locally
    conn_type = str(entry.get("connectionType") or "manual")
    if conn_type == "oauth" and (
        secrets_payload.get("token") or secrets_payload.get("access_token")
    ):
        conn_type = "manual"

    rows = _load_connections()
    existing = next((c for c in rows if c.server_name == name), None)
    if existing:
        existing.display_name = str(disp)
        existing.config = cfg
        existing.public_fields = public_fields
        existing.connection_type = conn_type
        existing.enabled = True
        existing.status = "connected"
        existing.last_error = None
        conn = existing
        updated = True
    else:
        conn = McpConnectionLocal(
            id=str(uuid4()),
            server_name=name,
            display_name=str(disp),
            connection_type=conn_type,
            enabled=True,
            status="connected",
            config=cfg,
            public_fields=public_fields,
        )
        rows.append(conn)
        updated = False

    _save_connections(rows)
    if secrets_payload:
        all_secrets = _load_secrets()
        all_secrets[conn.id] = secrets_payload
        _save_secrets(all_secrets)

    # Prefer real MCP handshake when runtime enabled
    test = test_connection(conn.id, prefer_runtime=True)
    if not test.get("ok"):
        conn.status = "error"
        conn.last_error = str(test.get("error") or "Connection test failed")
        _save_connections(rows)
    else:
        conn.status = "connected"
        conn.last_error = None
        _save_connections(rows)

    return {"connection": conn.to_public_dict(), "updated": updated, "test": test}


def disconnect(connection_id: str) -> dict[str, Any]:
    rows = _load_connections()
    before = len(rows)
    rows = [c for c in rows if c.id != connection_id]
    if len(rows) == before:
        raise ValueError(f"Connection not found: {connection_id}")
    _save_connections(rows)
    secrets = _load_secrets()
    if connection_id in secrets:
        del secrets[connection_id]
        _save_secrets(secrets)
    try:
        from aider.z.mcp_runtime import get_session_manager

        get_session_manager().drop_session(connection_id)
    except Exception:
        pass
    return {"ok": True, "id": connection_id}


def test_connection(
    connection_id: str, *, prefer_runtime: bool = True
) -> dict[str, Any]:
    rows = _load_connections()
    conn = next((c for c in rows if c.id == connection_id), None)
    if conn is None:
        return {"ok": False, "error": f"Connection not found: {connection_id}"}

    soft_err = None
    if prefer_runtime:
        try:
            from aider.z.mcp_runtime import get_session_manager, runtime_enabled

            if runtime_enabled():
                probed = get_session_manager().probe(connection_id)
                if probed.get("ok"):
                    return probed
                soft_err = str(probed.get("error") or "runtime handshake failed")
        except Exception as err:
            soft_err = str(err)

    cfg = dict(conn.config)
    url = str(cfg.get("url") or conn.public_fields.get("url") or "").strip()
    command = str(cfg.get("command") or conn.public_fields.get("command") or "").strip()

    if url:
        result = _test_http(url)
        if soft_err and not result.get("ok"):
            result["runtimeError"] = soft_err
        return result

    if command:
        # Resolve first token if space-separated
        exe = command.split()[0] if command else ""
        if shutil.which(exe) or Path(exe).exists():
            out = {"ok": True, "mode": "stdio", "command": command, "resolved": True}
            if soft_err:
                out["note"] = f"Soft PATH ok; runtime handshake failed: {soft_err}"
            return out
        # npx/node common for MCP — soft-ok if node exists
        if exe in {"npx", "npm", "node"} and shutil.which("node"):
            return {
                "ok": True,
                "mode": "stdio",
                "command": command,
                "resolved": True,
                "note": "node available; package will resolve at runtime"
                + (f"; handshake: {soft_err}" if soft_err else ""),
            }
        return {
            "ok": False,
            "mode": "stdio",
            "error": soft_err or f"Command not found on PATH: {exe}",
        }

    # Credential-only servers (e.g. github token present)
    secrets = _load_secrets().get(conn.id) or {}
    if secrets or conn.public_fields:
        return {
            "ok": True,
            "mode": "credentials",
            "note": soft_err
            or "Credentials stored; runtime probe deferred to tool use.",
        }

    return {"ok": False, "error": soft_err or "No command, URL, or credentials to test."}


def _test_http(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
        return {"ok": True, "mode": "http", "status": code, "url": url}
    except urllib.error.HTTPError as exc:
        # Many MCP HTTP endpoints return 4xx on bare GET — still reachable
        if 400 <= int(exc.code) < 500:
            return {
                "ok": True,
                "mode": "http",
                "status": int(exc.code),
                "url": url,
                "note": "Endpoint reachable (auth/method may be required).",
            }
        return {"ok": False, "mode": "http", "error": str(exc), "url": url}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "mode": "http", "error": str(exc), "url": url}


def _first_use_key(server_name: str, tool_name: str) -> str:
    return f"{server_name.strip()}::{tool_name.strip()}"


def needs_first_use_confirm(server_name: str, tool_name: str) -> bool:
    data = _read_json(first_use_path(), {"confirmed": {}})
    confirmed = data.get("confirmed") if isinstance(data, dict) else {}
    if not isinstance(confirmed, dict):
        return True
    return _first_use_key(server_name, tool_name) not in confirmed


def mark_first_use_confirmed(
    server_name: str,
    tool_name: str,
    *,
    forever: bool = True,
) -> dict[str, Any]:
    data = _read_json(first_use_path(), {"confirmed": {}})
    if not isinstance(data, dict):
        data = {"confirmed": {}}
    confirmed = data.setdefault("confirmed", {})
    if not isinstance(confirmed, dict):
        confirmed = {}
        data["confirmed"] = confirmed
    key = _first_use_key(server_name, tool_name)
    confirmed[key] = {
        "forever": bool(forever),
        "serverName": server_name,
        "toolName": tool_name,
    }
    _write_json(first_use_path(), data)
    return {"ok": True, "key": key, "forever": bool(forever)}


def first_use_status(server_name: str, tool_name: str) -> dict[str, Any]:
    needs = needs_first_use_confirm(server_name, tool_name)
    return {
        "serverName": server_name,
        "toolName": tool_name,
        "needsConfirm": needs,
        "confirmed": not needs,
    }


def sync_to_cloud(*, timeout: float = 20.0) -> dict[str, Any]:
    """
    Best-effort push of local manual connections to ``POST /v1/mcp/connect``.
    OAuth connections are skipped (must be completed on web).
    """
    creds = load_credentials()
    if creds is None or not creds.access_token:
        return {"ok": False, "error": "Not signed in", "synced": 0, "skipped": 0}

    rows = _load_connections()
    secrets_all = _load_secrets()
    synced = 0
    skipped = 0
    errors: list[str] = []

    for conn in rows:
        if conn.connection_type == "oauth":
            skipped += 1
            continue
        # Map custom-* back to catalog "custom" for API
        api_name = conn.server_name
        if api_name.startswith("custom-"):
            api_name = "custom"
        entry = get_catalog_entry(api_name)
        if entry is None:
            skipped += 1
            continue

        body = {
            "server_name": api_name,
            "display_name": conn.display_name,
            "scope": "personal",
            "config": conn.config,
            "credentials": {
                **(secrets_all.get(conn.id) or {}),
                **conn.public_fields,
            },
        }
        url = f"{get_auth_base_url()}/v1/mcp/connect"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {creds.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            remote = payload.get("connection") if isinstance(payload, dict) else None
            if isinstance(remote, dict) and remote.get("id"):
                conn.remote_id = str(remote["id"])
                synced += 1
            else:
                synced += 1
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"{conn.server_name}: {exc}")

    _save_connections(rows)
    return {
        "ok": len(errors) == 0,
        "synced": synced,
        "skipped": skipped,
        "errors": errors,
    }


def runtime_tools_payload() -> list[dict[str, Any]]:
    """Shape compatible with mcp_client / CLI runtime list."""
    out: list[dict[str, Any]] = []
    secrets_all = _load_secrets()
    for conn in _load_connections():
        if not conn.enabled:
            continue
        out.append(
            {
                "id": conn.id,
                "server_name": conn.server_name,
                "display_name": conn.display_name,
                "connection_type": conn.connection_type,
                "enabled": conn.enabled,
                "status": conn.status,
                "config": conn.config,
                "credentials": secrets_all.get(conn.id) or {},
            }
        )
    return out
