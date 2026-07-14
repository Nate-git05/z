"""Skill index + keyword relevance matching for auto-pull."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Set

from .schema import SkillIndexEntry

# Common stopwords for keyword overlap
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
    """
    Simple keyword overlap score in [0, 1].
    Title hits weighted higher than description hits.
    """
    task_tokens = tokenize(task)
    if not task_tokens:
        return 0.0
    title_tokens = tokenize(entry.title)
    desc_tokens = tokenize(entry.description)
    if not title_tokens and not desc_tokens:
        return 0.0

    title_hits = len(task_tokens & title_tokens)
    desc_hits = len(task_tokens & desc_tokens)
    title_den = max(len(title_tokens), 1)
    desc_den = max(len(desc_tokens), 1)

    score = 0.0
    if title_tokens:
        score += 0.7 * (title_hits / title_den)
    if desc_tokens:
        score += 0.3 * (desc_hits / desc_den)

    # Bonus if any strong title token appears in the task
    if title_hits:
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


def entries_from_local_index(rows: Iterable[dict]) -> List[SkillIndexEntry]:
    out: List[SkillIndexEntry] = []
    for r in rows:
        out.append(
            SkillIndexEntry(
                id=str(r.get("id") or ""),
                title=r.get("title") or "",
                description=r.get("description") or "",
                scope=r.get("scope") or "personal",
                source="local",
                remote_id=r.get("remote_id"),
                filename=r.get("filename"),
            )
        )
    return out


def entries_from_remote_index(rows: Iterable[dict]) -> List[SkillIndexEntry]:
    out: List[SkillIndexEntry] = []
    for r in rows:
        out.append(
            SkillIndexEntry(
                id=str(r.get("id") or ""),
                title=r.get("title") or "",
                description=r.get("description") or "",
                scope=r.get("scope") or "personal",
                source="remote",
                remote_id=str(r.get("id") or ""),
                filename=None,
            )
        )
    return out


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
        by_key[key] = e  # local wins
    return list(by_key.values())
