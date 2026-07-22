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


# scaffold = one-shot bootstrap; playbook = reusable ongoing guidance;
# bug_pattern = reusable diagnosis (symptom → root cause → fix technique)
SKILL_KIND_SCAFFOLD = "scaffold"
SKILL_KIND_PLAYBOOK = "playbook"
SKILL_KIND_BUG_PATTERN = "bug_pattern"

VALID_SKILL_KINDS = frozenset(
    {SKILL_KIND_SCAFFOLD, SKILL_KIND_PLAYBOOK, SKILL_KIND_BUG_PATTERN}
)


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
    source: str = "generate"  # paste | generate | capture | manual
    # Router fields
    kind: str = SKILL_KIND_PLAYBOOK  # scaffold | playbook | bug_pattern
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
    # Feature playbooks/scaffolds stamp the current root so project A skills
    # do not auto-apply (and rewrite files) in project B.
    # bug_pattern skills default to shared=True / empty repo_key — they are
    # symptom-based and portable across codebases by design.
    repo_key: str = ""
    shared: bool = False
    # bug_pattern fields — symptom is what gets embedded for retrieval
    symptom_description: str = ""
    root_cause_category: str = ""
    root_cause_explanation: str = ""
    fix_technique: str = ""
    verification_method: str = ""
    language: str = ""  # coarse applicability filter (also mirrored into languages)
    # Set only when category is known but evidence_regex missed the diff —
    # lets accept() confirm a taxonomy blind-spot candidate. Never auto-edits
    # bug_concepts.py; humans review via `z taxonomy review`.
    grounding_miss_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def embed_text(self) -> str:
        """Text embedded into ChromaDB for retrieval."""
        if (self.kind or "") == SKILL_KIND_BUG_PATTERN:
            # Embed the transferable symptom — not the fix — for cross-repo match
            parts = [
                self.symptom_description or self.description or "",
                self.root_cause_category or "",
                self.title or "",
                self.language or " ".join(self.languages or []),
                " ".join(self.tags or []),
                "bug_pattern",
            ]
            return "\n".join(p for p in parts if p).strip()
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
            "symptom_description": self.symptom_description or "",
            "root_cause_category": self.root_cause_category or "",
            "root_cause_explanation": self.root_cause_explanation or "",
            "fix_technique": self.fix_technique or "",
            "verification_method": self.verification_method or "",
            "language": self.language or "",
            "grounding_miss_reason": self.grounding_miss_reason or "",
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
            "symptom_description": self.symptom_description or "",
            "root_cause_category": self.root_cause_category or "",
            "root_cause_explanation": self.root_cause_explanation or "",
            "fix_technique": self.fix_technique or "",
            "verification_method": self.verification_method or "",
            "language": self.language or "",
            "grounding_miss_reason": self.grounding_miss_reason or "",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Skill":
        kind = (data.get("kind") or SKILL_KIND_PLAYBOOK).strip().lower()
        if kind not in VALID_SKILL_KINDS:
            kind = SKILL_KIND_PLAYBOOK
        apply_once = data.get("apply_once")
        if apply_once is None:
            apply_once = kind == SKILL_KIND_SCAFFOLD
        language = (data.get("language") or "").strip().lower()
        languages = _as_str_list(data.get("languages"))
        if language and language not in languages:
            languages = [language] + languages
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
            languages=languages,
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
            symptom_description=(data.get("symptom_description") or "").strip(),
            root_cause_category=(data.get("root_cause_category") or "").strip(),
            root_cause_explanation=(data.get("root_cause_explanation") or "").strip(),
            fix_technique=(data.get("fix_technique") or "").strip(),
            verification_method=(data.get("verification_method") or "").strip(),
            language=language or (languages[0] if languages else ""),
            grounding_miss_reason=(
                (data.get("grounding_miss_reason") or "").strip() or None
            ),
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
    symptom_description: str = ""
    root_cause_category: str = ""
    root_cause_explanation: str = ""
    fix_technique: str = ""
    verification_method: str = ""
    language: str = ""

    def match_text(self) -> str:
        bits = [
            self.title,
            self.description,
            self.symptom_description or "",
            self.root_cause_category or "",
            " ".join(self.tags or []),
            " ".join(self.triggers or []),
            " ".join(self.project_types or []),
            " ".join(self.languages or []),
            self.kind or "",
        ]
        return " ".join(b for b in bits if b).lower()
