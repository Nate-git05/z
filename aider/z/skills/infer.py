"""Infer skill metadata from pasted or generated content. Z owns metadata."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Set

from .schema import Skill, _as_str_list

_STOP = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "for",
    "to",
    "of",
    "in",
    "on",
    "with",
    "how",
    "this",
    "that",
    "our",
    "my",
    "we",
    "you",
    "is",
    "are",
    "be",
    "as",
    "at",
    "by",
    "from",
    "into",
    "about",
    "repo",
    "code",
    "using",
    "use",
    "please",
    "add",
    "make",
    "create",
    "skill",
    "steps",
    "when",
    "should",
    "must",
    "will",
    "can",
    "your",
    "their",
}


_PROJECT_HINTS = {
    "api": {"api", "endpoint", "rest", "graphql", "http", "webhook", "fastapi", "flask"},
    "backend": {"backend", "server", "database", "sql", "migration", "auth", "worker"},
    "frontend": {"frontend", "react", "css", "ui", "component", "dom", "browser"},
    "mobile": {"ios", "android", "mobile", "swift", "kotlin"},
    "infra": {"docker", "kubernetes", "deploy", "ci", "terraform", "aws"},
    "data": {"etl", "pipeline", "spark", "warehouse", "analytics"},
}


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_][a-z0-9_-]{2,}", (text or "").lower())


def _unique(items: Iterable[str], *, limit: int = 12) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen or key in _STOP:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= limit:
            break
    return out


def infer_title(content: str, *, fallback: str = "Untitled skill") -> str:
    for line in (content or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:120] or fallback
        return stripped[:120]
    return fallback


def infer_description(content: str, *, title: str = "") -> str:
    lines = [ln.strip() for ln in (content or "").splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("#"):
            continue
        if ln.lower().startswith(("when to use", "steps", "example")):
            continue
        return ln[:400]
    if title:
        return f"Skill for {title}"[:400]
    return "Reusable coding skill"


def infer_tags_and_triggers(content: str, *, title: str = "", description: str = "") -> tuple[list[str], list[str]]:
    blob = f"{title}\n{description}\n{content}"
    toks = _tokens(blob)
    # Prefer informative tokens; keep order of first appearance
    tags = _unique(toks, limit=8)
    # Triggers lean on shorter actionable nouns already in tags + title tokens
    title_toks = _unique(_tokens(title), limit=6)
    triggers = _unique(list(title_toks) + tags, limit=10)
    return tags, triggers


def infer_project_types(content: str, *, title: str = "", description: str = "", tags: Optional[List[str]] = None) -> List[str]:
    blob = set(_tokens(f"{title}\n{description}\n{content}\n{' '.join(tags or [])}"))
    found: List[str] = []
    for ptype, hints in _PROJECT_HINTS.items():
        if blob & hints:
            found.append(ptype)
    return found or ["general"]


def infer_metadata(
    content: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
    project_types: Optional[List[str]] = None,
    triggers: Optional[List[str]] = None,
    source: str = "paste",
) -> dict:
    """
    Fill missing metadata fields. Existing non-empty user/model values win.
    """
    body = (content or "").strip()
    resolved_title = (title or "").strip() or infer_title(body)
    resolved_desc = (description or "").strip() or infer_description(body, title=resolved_title)
    resolved_tags = _as_str_list(tags)
    resolved_triggers = _as_str_list(triggers)
    resolved_ptypes = _as_str_list(project_types)

    if not resolved_tags or not resolved_triggers:
        auto_tags, auto_triggers = infer_tags_and_triggers(
            body, title=resolved_title, description=resolved_desc
        )
        if not resolved_tags:
            resolved_tags = auto_tags
        if not resolved_triggers:
            resolved_triggers = auto_triggers

    if not resolved_ptypes:
        resolved_ptypes = infer_project_types(
            body, title=resolved_title, description=resolved_desc, tags=resolved_tags
        )

    return {
        "title": resolved_title,
        "description": resolved_desc,
        "tags": resolved_tags,
        "project_types": resolved_ptypes,
        "triggers": resolved_triggers,
        "source": source,
    }


def apply_inferred_metadata(skill: Skill, *, source: Optional[str] = None) -> Skill:
    meta = infer_metadata(
        skill.content,
        title=skill.title,
        description=skill.description,
        tags=skill.tags,
        project_types=skill.project_types,
        triggers=skill.triggers,
        source=source or skill.source or "paste",
    )
    skill.title = meta["title"]
    skill.description = meta["description"]
    skill.tags = meta["tags"]
    skill.project_types = meta["project_types"]
    skill.triggers = meta["triggers"]
    skill.source = meta["source"]
    return skill
