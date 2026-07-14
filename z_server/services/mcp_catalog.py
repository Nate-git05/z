"""Catalog of MCP servers available to connect in the Z dashboard."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class McpToolCatalogEntry:
    server_name: str
    display_name: str
    description: str
    connection_type: str  # oauth | manual
    # Manual form fields: [{key, label, secret, placeholder, required}]
    fields: list[dict[str, Any]] = field(default_factory=list)
    # OAuth metadata (authorization URL template etc.)
    oauth: dict[str, Any] = field(default_factory=dict)
    # Default non-secret config
    default_config: dict[str, Any] = field(default_factory=dict)
    docs_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Curated starters — users can also add a custom MCP server via "custom"
MCP_CATALOG: list[McpToolCatalogEntry] = [
    McpToolCatalogEntry(
        server_name="github",
        display_name="GitHub",
        description="Repos, issues, and PRs via the GitHub MCP server.",
        connection_type="oauth",
        oauth={
            "authorize_url": "https://github.com/login/oauth/authorize",
            "token_url": "https://github.com/login/oauth/access_token",
            "scopes": "repo read:user",
            "client_id_env": "Z_MCP_GITHUB_CLIENT_ID",
            "client_secret_env": "Z_MCP_GITHUB_CLIENT_SECRET",
        },
        default_config={
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
        },
        docs_url="https://github.com/modelcontextprotocol/servers",
    ),
    McpToolCatalogEntry(
        server_name="filesystem",
        display_name="Filesystem",
        description="Read/write files in an allowed directory via MCP.",
        connection_type="manual",
        fields=[
            {
                "key": "root_path",
                "label": "Allowed root path",
                "secret": False,
                "placeholder": "/workspace",
                "required": True,
            },
        ],
        default_config={
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        },
    ),
    McpToolCatalogEntry(
        server_name="postgres",
        display_name="PostgreSQL",
        description="Query a Postgres database through MCP.",
        connection_type="manual",
        fields=[
            {
                "key": "database_url",
                "label": "Database URL",
                "secret": True,
                "placeholder": "postgresql://user:pass@host:5432/db",
                "required": True,
            },
        ],
        default_config={
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-postgres"],
        },
    ),
    McpToolCatalogEntry(
        server_name="slack",
        display_name="Slack",
        description="Read channels and post messages via Slack MCP.",
        connection_type="manual",
        fields=[
            {
                "key": "bot_token",
                "label": "Bot token",
                "secret": True,
                "placeholder": "xoxb-…",
                "required": True,
            },
            {
                "key": "team_id",
                "label": "Team / workspace ID",
                "secret": False,
                "placeholder": "T0…",
                "required": False,
            },
        ],
        default_config={"transport": "stdio"},
    ),
    McpToolCatalogEntry(
        server_name="custom",
        display_name="Custom MCP server",
        description="Connect any MCP server by URL or command.",
        connection_type="manual",
        fields=[
            {
                "key": "server_url",
                "label": "Server URL (SSE/HTTP)",
                "secret": False,
                "placeholder": "https://mcp.example.com/sse",
                "required": False,
            },
            {
                "key": "command",
                "label": "Local command (stdio)",
                "secret": False,
                "placeholder": "npx -y my-mcp-server",
                "required": False,
            },
            {
                "key": "api_key",
                "label": "API key / bearer token",
                "secret": True,
                "placeholder": "",
                "required": False,
            },
            {
                "key": "label",
                "label": "Display label",
                "secret": False,
                "placeholder": "My MCP",
                "required": True,
            },
        ],
        default_config={"transport": "sse"},
    ),
]


def get_catalog_entry(server_name: str) -> McpToolCatalogEntry | None:
    for entry in MCP_CATALOG:
        if entry.server_name == server_name:
            return entry
    return None


def list_catalog() -> list[dict[str, Any]]:
    return [e.to_dict() for e in MCP_CATALOG]
