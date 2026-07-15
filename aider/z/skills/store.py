"""Local skill store under ~/.z/skills/ — markdown with YAML frontmatter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List, Optional

import yaml

from aider.z.paths import ensure_z_home

from .schema import Skill, _as_str_list, slugify

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def skills_dir() -> Path:
    d = ensure_z_home() / "skills"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def chroma_dir() -> Path:
    d = ensure_z_home() / "chroma" / "skills"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _dump_frontmatter(skill: Skill) -> str:
    meta: dict[str, Any] = {
        "id": skill.id,
        "title": skill.title,
        "description": skill.description,
        "tags": list(skill.tags or []),
        "project_types": list(skill.project_types or []),
        "triggers": list(skill.triggers or []),
        "languages": list(skill.languages or []),
        "kind": skill.kind or "playbook",
        "artifacts": list(skill.artifacts or []),
        "apply_once": bool(skill.apply_once),
        "capability": skill.capability or "",
        "grounded_symbols": list(skill.grounded_symbols or []),
        "source_files": list(skill.source_files or []),
        "needs_review": bool(skill.needs_review),
        "quality_state": skill.quality_state or "verified",
        "path": skill.path or "",
        "source": skill.source or "generate",
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
        "scope": skill.scope,
    }
    if skill.grounded_at:
        meta["grounded_at"] = skill.grounded_at
    if skill.content_hash:
        meta["content_hash"] = skill.content_hash
    if skill.created_by:
        meta["created_by"] = skill.created_by
    if skill.remote_id:
        meta["remote_id"] = skill.remote_id
    if skill.workspace_id:
        meta["workspace_id"] = skill.workspace_id
    dumped = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n"


def skill_to_markdown(skill: Skill) -> str:
    return _dump_frontmatter(skill) + (skill.content or "").lstrip("\n") + "\n"


def skill_from_markdown(text: str, *, filename: Optional[str] = None) -> Skill:
    raw = text.strip()
    if not raw.endswith("\n"):
        raw += "\n"
    m = FRONTMATTER_RE.match(raw)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        body = m.group(2).strip()
    else:
        meta = {}
        body = text.strip()
        first = body.splitlines()[0] if body else "Untitled skill"
        meta["title"] = first.lstrip("# ").strip() or "Untitled skill"
        meta["description"] = ""

    kind = (meta.get("kind") or "playbook")
    if isinstance(kind, str):
        kind = kind.strip().lower()
    else:
        kind = "playbook"
    if kind not in ("scaffold", "playbook"):
        kind = "playbook"
    apply_once = meta.get("apply_once")
    if apply_once is None:
        apply_once = kind == "scaffold"
    return Skill(
        id=str(meta.get("id") or ""),
        title=meta.get("title") or "Untitled skill",
        description=meta.get("description") or "",
        content=body,
        created_at=meta.get("created_at") or "",
        updated_at=meta.get("updated_at") or meta.get("created_at") or "",
        created_by=meta.get("created_by"),
        scope=meta.get("scope") or "personal",
        remote_id=meta.get("remote_id"),
        workspace_id=meta.get("workspace_id"),
        filename=filename,
        path=meta.get("path") or None,
        tags=_as_str_list(meta.get("tags")),
        project_types=_as_str_list(meta.get("project_types")),
        triggers=_as_str_list(meta.get("triggers")),
        source=meta.get("source") or "generate",
        kind=kind,
        languages=_as_str_list(meta.get("languages")),
        artifacts=_as_str_list(meta.get("artifacts")),
        apply_once=bool(apply_once),
        capability=str(meta.get("capability") or "").strip(),
        grounded_symbols=_as_str_list(meta.get("grounded_symbols")),
        source_files=_as_str_list(meta.get("source_files")),
        needs_review=bool(meta.get("needs_review")),
        quality_state=(
            str(meta.get("quality_state") or "").strip().lower()
            if str(meta.get("quality_state") or "").strip().lower()
            in ("draft", "verified", "rejected")
            else ("draft" if meta.get("needs_review") else "verified")
        ),
        grounded_at=meta.get("grounded_at"),
        content_hash=meta.get("content_hash"),
    )


class LocalSkillStore:
    """Scan / read / write skills on disk."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else skills_dir()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def list_skills(self) -> List[Skill]:
        skills: List[Skill] = []
        for path in sorted(self.root.glob("*.md")):
            try:
                skill = self.read_file(path)
                if skill:
                    skills.append(skill)
            except OSError:
                continue
        return skills

    def read_file(self, path: Path) -> Optional[Skill]:
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        skill = skill_from_markdown(text, filename=path.name)
        if not skill.id:
            skill.id = path.stem
        if not skill.created_at:
            from datetime import datetime, timezone

            skill.created_at = datetime.now(timezone.utc).isoformat()
            skill.updated_at = skill.created_at
        skill.path = str(path.resolve())
        skill.filename = path.name
        return skill

    def get(self, skill_id: str) -> Optional[Skill]:
        for skill in self.list_skills():
            if skill.id == skill_id or skill.filename == f"{skill_id}.md":
                return skill
            if skill.remote_id and skill.remote_id == skill_id:
                return skill
            if skill.title.lower() == skill_id.lower():
                return skill
        path = self.root / f"{skill_id}.md"
        if path.is_file():
            return self.read_file(path)
        # Match by short id suffix in filename
        for path in self.root.glob(f"*-{skill_id[:8]}.md"):
            return self.read_file(path)
        return None

    def get_by_path(self, path: str | Path) -> Optional[Skill]:
        p = Path(path)
        if p.is_file():
            return self.read_file(p)
        return None

    def save(self, skill: Skill) -> Path:
        from datetime import datetime, timezone

        skill.updated_at = datetime.now(timezone.utc).isoformat()
        if not skill.created_at:
            skill.created_at = skill.updated_at
        if not skill.id:
            import uuid

            skill.id = str(uuid.uuid4())
        if not skill.filename:
            skill.filename = f"{slugify(skill.title)}-{skill.id[:8]}.md"
        path = self.root / skill.filename
        skill.path = str(path.resolve())
        path.write_text(skill_to_markdown(skill), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def delete(self, skill_id: str) -> bool:
        skill = self.get(skill_id)
        if not skill or not skill.filename:
            return False
        path = self.root / skill.filename
        if path.is_file():
            path.unlink()
            return True
        return False

    def index(self) -> List[dict]:
        return [s.index_entry() for s in self.list_skills()]
