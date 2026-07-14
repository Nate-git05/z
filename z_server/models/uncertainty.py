"""Uncertainty tree persistence — workspace-visible memory layer."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from z_server.models.base import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


class UncertaintyTask(Base):
    """A coding task / feature area that owns a checklist and nodes."""

    __tablename__ = "uncertainty_tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    repo_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    checklist: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_by_session: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    nodes = relationship("UncertaintyNodeRow", back_populates="task", cascade="all, delete-orphan")


class UncertaintyNodeRow(Base):
    """Persisted uncertainty node — survives across sessions as the risk memory layer."""

    __tablename__ = "uncertainty_nodes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("uncertainty_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    repo_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    node_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    confidence_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    files_affected: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    symbols_affected: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    why_uncertain: Mapped[str] = mapped_column(Text, nullable=False, default="")
    what_could_go_wrong: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_fix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_tests: Mapped[list[Any]] = mapped_column(JsonType, nullable=False, default=list)
    suggested_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="Open", index=True)
    area: Mapped[str] = mapped_column(String(40), nullable=False, default="Other")
    signals: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)

    created_by_session: Mapped[str | None] = mapped_column(String(100), nullable=True)
    escalation_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    escalated_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    task = relationship("UncertaintyTask", back_populates="nodes")

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "title": self.title,
            "type": self.node_type,
            "confidence_tier": self.confidence_tier,
            "risk_tier": self.risk_tier,
            "summary": self.summary,
            "explanation": self.explanation,
            "files_affected": self.files_affected or [],
            "symbols_affected": self.symbols_affected or [],
            "why_uncertain": self.why_uncertain,
            "what_could_go_wrong": self.what_could_go_wrong,
            "suggested_fix": self.suggested_fix,
            "suggested_tests": self.suggested_tests or [],
            "suggested_prompt": self.suggested_prompt,
            "status": self.status,
            "area": self.area,
            "task_id": str(self.task_id) if self.task_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "created_by_session": self.created_by_session,
            "created_by_user": str(self.created_by_user_id) if self.created_by_user_id else None,
            "escalation_status": self.escalation_status,
            "signals": self.signals or {},
            "workspace_id": str(self.workspace_id) if self.workspace_id else None,
            "repo_key": self.repo_key,
        }
