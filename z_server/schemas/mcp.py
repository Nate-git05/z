"""Pydantic schemas for MCP connection APIs."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class McpConnectRequest(BaseModel):
    server_name: str
    scope: str = Field(default="personal", description="personal or workspace")
    # Manual fields (secrets + non-secrets mixed; secrets go to encrypted_credentials)
    credentials: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    display_name: str | None = None


class McpConnectionOut(BaseModel):
    id: UUID
    server_name: str
    display_name: str | None
    connection_type: str
    scope: str
    config: dict[str, Any]
    enabled: bool
    status: str
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True
