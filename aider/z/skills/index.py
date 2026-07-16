"""Skill index helpers — keyword fallback when ChromaDB is unavailable."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Set

from .schema import SkillIndexEntry, _as_str_list

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
}


def tokenize(text: str) -> Set[str]:
    words = re.findall(r"[a-z0-9_]{3,}", (text or "").lower())
    return {w for w in words if w not in _STOP}


def relevance_score(task: str, entry: SkillIndexEntry) -> float:
    """Keyword overlap score in [0, 1] — fallback when vector search is down."""
    task_tokens = tokenize(task)
    if not task_tokens:
        return 0.0
    title_tokens = tokenize(entry.title)
    desc_tokens = tokenize(entry.description)
    extra_tokens = tokenize(
        " ".join(
            [
                " ".join(entry.tags or []),
                " ".join(entry.triggers or []),
                " ".join(entry.project_types or []),
                " ".join(entry.languages or []),
                entry.kind or "",
            ]
        )
    )
    if not title_tokens and not desc_tokens and not extra_tokens:
        return 0.0

    title_hits = len(task_tokens & title_tokens)
    desc_hits = len(task_tokens & desc_tokens)
    extra_hits = len(task_tokens & extra_tokens)
    title_den = max(len(title_tokens), 1)
    desc_den = max(len(desc_tokens), 1)
    extra_den = max(len(extra_tokens), 1)

    score = 0.0
    if title_tokens:
        score += 0.55 * (title_hits / title_den)
    if desc_tokens:
        score += 0.25 * (desc_hits / desc_den)
    if extra_tokens:
        score += 0.20 * (extra_hits / extra_den)

    if title_hits or extra_hits:
        score = min(1.0, score + 0.15)
    return score


DEFAULT_THRESHOLD = 0.35


def match_skills(
    task: str,
    index: Sequence[SkillIndexEntry],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = 3,
) -> List[tuple[SkillIndexEntry, float]]:
    scored = [(e, relevance_score(task, e)) for e in index]
    scored = [(e, s) for e, s in scored if s >= threshold]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def _entry_from_row(r: dict, *, source: str) -> SkillIndexEntry:
    kind = (r.get("kind") or "playbook")
    if isinstance(kind, str):
        kind = kind.strip().lower()
    else:
        kind = "playbook"
    if kind not in ("scaffold", "playbook"):
        kind = "playbook"
    apply_once = r.get("apply_once")
    if apply_once is None:
        apply_once = kind == "scaffold"
    return SkillIndexEntry(
        id=str(r.get("id") or ""),
        title=r.get("title") or "",
        description=r.get("description") or "",
        scope=r.get("scope") or "personal",
        source=source,
        remote_id=r.get("remote_id") or (str(r.get("id")) if source == "remote" else None),
        filename=r.get("filename"),
        path=r.get("path"),
        tags=_as_str_list(r.get("tags")),
        project_types=_as_str_list(r.get("project_types")),
        triggers=_as_str_list(r.get("triggers")),
        languages=_as_str_list(r.get("languages")),
        kind=kind,
        artifacts=_as_str_list(r.get("artifacts")),
        apply_once=bool(apply_once),
        capability=str(r.get("capability") or "").strip(),
        grounded_symbols=_as_str_list(r.get("grounded_symbols")),
        source_files=_as_str_list(r.get("source_files")),
        needs_review=bool(r.get("needs_review")),
        quality_state=(
            str(r.get("quality_state") or "").strip().lower()
            if str(r.get("quality_state") or "").strip().lower()
            in ("draft", "verified", "rejected")
            else ("draft" if r.get("needs_review") else "verified")
        ),
        repo_key=str(r.get("repo_key") or "").strip(),
        shared=bool(r.get("shared")),
    )


def entries_from_local_index(rows: Iterable[dict]) -> List[SkillIndexEntry]:
    return [_entry_from_row(r, source="local") for r in rows]


def entries_from_remote_index(rows: Iterable[dict]) -> List[SkillIndexEntry]:
    return [_entry_from_row(r, source="remote") for r in rows]


def merge_index(
    local: Sequence[SkillIndexEntry],
    remote: Sequence[SkillIndexEntry],
) -> List[SkillIndexEntry]:
    """Prefer local copies when the same remote_id / title exists."""
    by_key: dict[str, SkillIndexEntry] = {}
    for e in remote:
        key = e.remote_id or e.id or e.title.lower()
        by_key[key] = e
    for e in local:
        key = e.remote_id or e.id or e.title.lower()
        by_key[key] = e
    return list(by_key.values())
