"""Reusable skills — personal or workspace-scoped instruction files."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from z_server.models.base import Base


class Skill(Base):
    """
    A reusable skill (plain content — no secrets).

    Scope mirrors McpConnection:
      - personal: user_id set, workspace_id null
      - workspace: workspace_id set (shared with members); user_id may remain
        as created_by attribution
    """

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

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

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", backref="skills")
    workspace = relationship("Workspace", backref="skills")

    @property
    def scope(self) -> str:
        return "workspace" if self.workspace_id else "personal"

    def to_index_dict(self) -> dict:
        return {
            "id": str(self.id),
            "title": self.title,
            "description": self.description,
            "scope": self.scope,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "workspace_id": str(self.workspace_id) if self.workspace_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
        }

    def to_api_dict(self) -> dict:
        d = self.to_index_dict()
        d["content"] = self.content
        return d
