"""Skill schema — reusable, model-generated instruction files."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(title: str) -> str:
    s = (title or "skill").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-") or "skill"
    return s[:60]


@dataclass
class Skill:
    title: str
    description: str
    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    created_by: Optional[str] = None
    scope: str = "personal"  # personal | workspace
    remote_id: Optional[str] = None  # id on z_server when synced
    workspace_id: Optional[str] = None
    filename: Optional[str] = None  # local basename

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def index_entry(self) -> dict[str, Any]:
        """Lightweight index row — title/description only (no full content)."""
        return {
            "id": self.id,
            "remote_id": self.remote_id,
            "title": self.title,
            "description": self.description,
            "scope": self.scope,
            "created_at": self.created_at,
            "filename": self.filename,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Skill":
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            title=data.get("title") or "Untitled skill",
            description=data.get("description") or "",
            content=data.get("content") or "",
            created_at=data.get("created_at") or _utcnow(),
            updated_at=data.get("updated_at") or data.get("created_at") or _utcnow(),
            created_by=data.get("created_by"),
            scope=data.get("scope") or "personal",
            remote_id=data.get("remote_id") or data.get("id"),
            workspace_id=data.get("workspace_id"),
            filename=data.get("filename"),
        )


@dataclass
class SkillIndexEntry:
    """Session-loaded lightweight skill descriptor (no body content)."""

    id: str
    title: str
    description: str
    scope: str = "personal"
    source: str = "local"  # local | remote
    remote_id: Optional[str] = None
    filename: Optional[str] = None

    def match_text(self) -> str:
        return f"{self.title} {self.description}".lower()
