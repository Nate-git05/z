"""Skill schema — reusable instruction files with retrieval metadata."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quality_state_from_data(data: dict[str, Any]) -> str:
    raw = (data.get("quality_state") or "").strip().lower()
    if raw in ("draft", "verified", "rejected"):
        return raw
    # Migrate legacy needs_review
    if data.get("needs_review"):
        return "draft"
    return "verified"


def slugify(title: str) -> str:
    s = (title or "skill").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-") or "skill"
    return s[:60]


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        # Support YAML-ish "[a, b]" or comma-separated
        if text.startswith("[") and text.endswith("]"):
            inner = text[1:-1].strip()
            if not inner:
                return []
            return [p.strip().strip("\"'") for p in inner.split(",") if p.strip()]
        return [p.strip() for p in text.split(",") if p.strip()]
    return [str(value).strip()]


# scaffold = one-shot bootstrap; playbook = reusable ongoing guidance
SKILL_KIND_SCAFFOLD = "scaffold"
SKILL_KIND_PLAYBOOK = "playbook"


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
    remote_id: Optional[str] = None
    workspace_id: Optional[str] = None
    filename: Optional[str] = None  # local basename
    path: Optional[str] = None  # absolute path to the skill file
    tags: List[str] = field(default_factory=list)
    project_types: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)
    source: str = "generate"  # paste | generate | capture
    # Router fields
    kind: str = SKILL_KIND_PLAYBOOK  # scaffold | playbook
    languages: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)  # paths that mean "already done"
    apply_once: bool = False
    # Grounding / capture provenance
    capability: str = ""  # short reusable capability label (not whole-app)
    grounded_symbols: List[str] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    needs_review: bool = False  # block auto-retrieve until user accepts
    # draft = capture pending accept; verified = retrievable; rejected = quarantine
    quality_state: str = "verified"
    grounded_at: Optional[str] = None
    content_hash: Optional[str] = None  # hash of grounding pack at capture
    # Repo isolation: bind skill to the project that produced it.
    # repo_key = resolved project root; empty + shared=True → apply anywhere.
    # Captures/generates stamp the current root so project A skills do not
    # auto-apply (and rewrite files) in project B.
    repo_key: str = ""
    shared: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def embed_text(self) -> str:
        """Text embedded into ChromaDB for retrieval."""
        parts = [
            self.title or "",
            self.description or "",
            " ".join(self.tags or []),
            " ".join(self.triggers or []),
            " ".join(self.project_types or []),
            " ".join(self.languages or []),
            self.kind or "",
        ]
        return "\n".join(p for p in parts if p).strip()

    def metadata_public(self) -> dict[str, Any]:
        """Fields shown when the user asks to see a new skill."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags or []),
            "project_types": list(self.project_types or []),
            "triggers": list(self.triggers or []),
            "languages": list(self.languages or []),
            "kind": self.kind,
            "artifacts": list(self.artifacts or []),
            "apply_once": self.apply_once,
            "capability": self.capability or "",
            "grounded_symbols": list(self.grounded_symbols or []),
            "source_files": list(self.source_files or []),
            "needs_review": bool(self.needs_review),
            "quality_state": self.quality_state or "verified",
            "path": self.path,
            "scope": self.scope,
            "source": self.source,
            "repo_key": self.repo_key or "",
            "shared": bool(self.shared),
            "created_at": self.created_at,
        }

    def index_entry(self) -> dict[str, Any]:
        """Lightweight index row — no full content."""
        return {
            "id": self.id,
            "remote_id": self.remote_id,
            "title": self.title,
            "description": self.description,
            "scope": self.scope,
            "created_at": self.created_at,
            "filename": self.filename,
            "path": self.path,
            "tags": list(self.tags or []),
            "project_types": list(self.project_types or []),
            "triggers": list(self.triggers or []),
            "languages": list(self.languages or []),
            "kind": self.kind,
            "artifacts": list(self.artifacts or []),
            "apply_once": self.apply_once,
            "capability": self.capability or "",
            "grounded_symbols": list(self.grounded_symbols or []),
            "source_files": list(self.source_files or []),
            "needs_review": bool(self.needs_review),
            "quality_state": self.quality_state or "verified",
            "source": self.source,
            "repo_key": self.repo_key or "",
            "shared": bool(self.shared),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Skill":
        kind = (data.get("kind") or SKILL_KIND_PLAYBOOK).strip().lower()
        if kind not in (SKILL_KIND_SCAFFOLD, SKILL_KIND_PLAYBOOK):
            kind = SKILL_KIND_PLAYBOOK
        apply_once = data.get("apply_once")
        if apply_once is None:
            apply_once = kind == SKILL_KIND_SCAFFOLD
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
            path=data.get("path"),
            tags=_as_str_list(data.get("tags")),
            project_types=_as_str_list(data.get("project_types")),
            triggers=_as_str_list(data.get("triggers")),
            source=data.get("source") or "generate",
            kind=kind,
            languages=_as_str_list(data.get("languages")),
            artifacts=_as_str_list(data.get("artifacts")),
            apply_once=bool(apply_once),
            capability=(data.get("capability") or "").strip(),
            grounded_symbols=_as_str_list(data.get("grounded_symbols")),
            source_files=_as_str_list(data.get("source_files")),
            needs_review=bool(data.get("needs_review")),
            quality_state=_quality_state_from_data(data),
            grounded_at=data.get("grounded_at"),
            content_hash=data.get("content_hash"),
            repo_key=str(data.get("repo_key") or "").strip(),
            shared=bool(data.get("shared")),
        )


@dataclass
class SkillIndexEntry:
    """Session-loaded lightweight skill descriptor (no body content)."""

    id: str
    title: str
    description: str
    scope: str = "personal"
    source: str = "local"  # local | remote | paste | generate | capture
    remote_id: Optional[str] = None
    filename: Optional[str] = None
    path: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    project_types: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    kind: str = SKILL_KIND_PLAYBOOK
    artifacts: List[str] = field(default_factory=list)
    apply_once: bool = False
    capability: str = ""
    grounded_symbols: List[str] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    needs_review: bool = False
    quality_state: str = "verified"
    repo_key: str = ""
    shared: bool = False

    def match_text(self) -> str:
        bits = [
            self.title,
            self.description,
            " ".join(self.tags or []),
            " ".join(self.triggers or []),
            " ".join(self.project_types or []),
            " ".join(self.languages or []),
            self.kind or "",
        ]
        return " ".join(b for b in bits if b).lower()
