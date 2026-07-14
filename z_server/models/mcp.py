"""MCP server connections — personal or workspace scoped."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from z_server.models.base import Base


class McpConnectionType(str, enum.Enum):
    oauth = "oauth"
    manual = "manual"


# JSON works on SQLite (tests); JSONB preferred on Postgres
JsonType = JSON().with_variant(JSONB(), "postgresql")


class McpConnection(Base):
    """
    A connected MCP server for a user (personal) or workspace (team).

    Exactly one of user_id / workspace_id should be set:
      - personal: user_id set, workspace_id null
      - workspace: workspace_id set, user_id null (shared by members)
    """

    __tablename__ = "mcp_connections"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "server_name",
            name="uq_mcp_user_server",
        ),
        UniqueConstraint(
            "workspace_id",
            "server_name",
            name="uq_mcp_workspace_server",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    server_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    connection_type: Mapped[McpConnectionType] = mapped_column(
        Enum(McpConnectionType, name="mcp_connection_type", native_enum=False),
        nullable=False,
    )

    # Fernet ciphertext (never store plaintext tokens/API keys)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Non-sensitive details: server URL, transport, metadata
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="connected", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", backref="mcp_connections")
    workspace = relationship("Workspace", backref="mcp_connections")

    @property
    def scope(self) -> str:
        return "workspace" if self.workspace_id else "personal"
