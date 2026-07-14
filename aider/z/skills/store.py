"""Local skill store under ~/.z/skills/ — markdown with YAML frontmatter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from aider.z.paths import ensure_z_home

from .schema import Skill, slugify

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def skills_dir() -> Path:
    d = ensure_z_home() / "skills"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _parse_frontmatter(raw: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta


def _dump_frontmatter(skill: Skill) -> str:
    lines = [
        "---",
        f"id: {skill.id}",
        f"title: {skill.title}",
        f"description: {skill.description}",
        f"created_at: {skill.created_at}",
        f"updated_at: {skill.updated_at}",
        f"scope: {skill.scope}",
    ]
    if skill.created_by:
        lines.append(f"created_by: {skill.created_by}")
    if skill.remote_id:
        lines.append(f"remote_id: {skill.remote_id}")
    if skill.workspace_id:
        lines.append(f"workspace_id: {skill.workspace_id}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def skill_to_markdown(skill: Skill) -> str:
    return _dump_frontmatter(skill) + (skill.content or "").lstrip("\n") + "\n"


def skill_from_markdown(text: str, *, filename: Optional[str] = None) -> Skill:
    m = FRONTMATTER_RE.match(text.strip() + ("\n" if not text.endswith("\n") else ""))
    if m:
        meta = _parse_frontmatter(m.group(1))
        body = m.group(2).strip()
    else:
        # Plain markdown fallback
        meta = {}
        body = text.strip()
        first = body.splitlines()[0] if body else "Untitled skill"
        meta["title"] = first.lstrip("# ").strip() or "Untitled skill"
        meta["description"] = ""

    return Skill(
        id=meta.get("id") or "",
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
        text = path.read_text(encoding="utf-8")
        skill = skill_from_markdown(text, filename=path.name)
        if not skill.id:
            # Stable id from filename when missing
            skill.id = path.stem
        if not skill.created_at:
            from datetime import datetime, timezone

            skill.created_at = datetime.now(timezone.utc).isoformat()
            skill.updated_at = skill.created_at
        return skill

    def get(self, skill_id: str) -> Optional[Skill]:
        for skill in self.list_skills():
            if skill.id == skill_id or skill.filename == f"{skill_id}.md":
                return skill
            if skill.remote_id and skill.remote_id == skill_id:
                return skill
        # Direct filename
        path = self.root / f"{skill_id}.md"
        if path.is_file():
            return self.read_file(path)
        return None

    def save(self, skill: Skill) -> Path:
        from datetime import datetime, timezone

        skill.updated_at = datetime.now(timezone.utc).isoformat()
        if not skill.created_at:
            skill.created_at = skill.updated_at
        if not skill.filename:
            skill.filename = f"{slugify(skill.title)}-{skill.id[:8]}.md"
        path = self.root / skill.filename
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
