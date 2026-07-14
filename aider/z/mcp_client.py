"""Fetch and expose MCP connections from the Z web backend for the CLI agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import requests

from aider.z.auth import current_session, get_auth_base_url
from aider.z.credentials import load_credentials  # noqa: F401 — re-export convenience


@dataclass
class McpToolRuntime:
    """A connected MCP server available to the agent for this session."""

    id: str
    server_name: str
    display_name: str
    connection_type: str
    scope: str
    config: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    status: str = "connected"

    def public_dict(self) -> dict[str, Any]:
        """Safe summary without secrets (for listings / announcements)."""
        return {
            "id": self.id,
            "server_name": self.server_name,
            "display_name": self.display_name,
            "connection_type": self.connection_type,
            "scope": self.scope,
            "config": {
                k: v
                for k, v in (self.config or {}).items()
                if k.lower() not in ("api_key", "token", "password", "secret")
            },
            "enabled": self.enabled,
            "status": self.status,
        }


# Session-level registry populated at CLI startup
_SESSION_TOOLS: list[McpToolRuntime] = []


def get_session_mcp_tools() -> list[McpToolRuntime]:
    return list(_SESSION_TOOLS)


def clear_session_mcp_tools() -> None:
    _SESSION_TOOLS.clear()


def fetch_mcp_runtime(access_token: str | None = None) -> list[McpToolRuntime]:
    """Pull enabled MCP connections from z_server for the signed-in account."""
    creds = current_session()
    token = access_token or (creds.access_token if creds else None) or os.environ.get(
        "Z_ACCESS_TOKEN"
    )
    if not token:
        return []

    base = get_auth_base_url()
    try:
        resp = requests.get(
            f"{base}/v1/mcp/runtime",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 401:
            return []
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException:
        return []

    tools: list[McpToolRuntime] = []
    for item in payload.get("tools") or []:
        tools.append(
            McpToolRuntime(
                id=str(item.get("id", "")),
                server_name=item.get("server_name") or "",
                display_name=item.get("display_name") or item.get("server_name") or "",
                connection_type=item.get("connection_type") or "manual",
                scope=item.get("scope") or "personal",
                config=item.get("config") or {},
                credentials=item.get("credentials") or {},
                enabled=bool(item.get("enabled", True)),
                status=item.get("status") or "connected",
            )
        )
    return tools


def load_mcp_tools_for_session(io=None) -> list[McpToolRuntime]:
    """Fetch from backend and store in the process-wide session registry."""
    global _SESSION_TOOLS
    tools = fetch_mcp_runtime()
    _SESSION_TOOLS = tools
    if io and tools:
        names = ", ".join(t.display_name or t.server_name for t in tools)
        io.tool_output(f"MCP tools connected: {names}")
    elif io and current_session():
        io.tool_output("MCP tools: none connected (manage at the Z web app → Integrations).")
    return tools


def print_mcp_list(io) -> None:
    """Read-only listing for `z mcp list`."""
    tools = fetch_mcp_runtime()
    if not current_session() and not os.environ.get("Z_ACCESS_TOKEN"):
        io.tool_output("Not signed in. Run `z login` first.")
        io.tool_output("Connect/disconnect MCP tools in the web app Integrations page.")
        return
    if not tools:
        io.tool_output("No MCP tools connected.")
        io.tool_output("Open the Z web app → Integrations to connect tools.")
        return
    io.tool_output(f"Connected MCP tools ({len(tools)}):")
    for t in tools:
        status = t.status if t.enabled else "disabled"
        io.tool_output(
            f"  • {t.display_name}  [{t.server_name}]  {t.scope}/{t.connection_type}  ({status})"
        )
    io.tool_output("")
    io.tool_output("Connect or disconnect tools in the web dashboard (not the CLI).")
