"""Lexical fallback + near-dup consolidation for skills.

Fault-plan ``skill-retrieve``: fold cache/eviction stems so LRU skills can
match LFU tasks when Chroma misses, and merge near-dup bug_pattern captures
into an existing skill id instead of cloning the library.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Set, Tuple

from .index import tokenize
from .schema import SKILL_KIND_BUG_PATTERN, Skill, SkillIndexEntry

# Token → family id. Tokens in the same family count as matches across
# LRU vs LFU vs generic "cache eviction" wording.
_STEM_FAMILIES: dict[str, str] = {
    "lru": "cache_policy",
    "lfu": "cache_policy",
    "mru": "cache_policy",
    "arc": "cache_policy",
    "tlru": "cache_policy",
    "cache": "cache",
    "caching": "cache",
    "cached": "cache",
    "evict": "eviction",
    "eviction": "eviction",
    "evicted": "eviction",
    "evicting": "eviction",
    "reclaim": "eviction",
    "reclamation": "eviction",
    "backing": "storage",
    "storage": "storage",
    "leak": "lifetime",
    "leaks": "lifetime",
    "dangling": "lifetime",
    "uaf": "lifetime",
    "use": "lifetime",  # use-after-free often tokenizes oddly; also see phrase
    "after": "lifetime",
    "free": "lifetime",
    "race": "concurrency",
    "races": "concurrency",
    "tsan": "concurrency",
    "thread": "concurrency",
    "atomic": "concurrency",
    "mutex": "concurrency",
}


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def lexical_fallback_enabled() -> bool:
    return _env_bool("Z_SKILL_LEXICAL_FALLBACK", True)


def near_dup_enabled() -> bool:
    return _env_bool("Z_SKILL_NEAR_DUP", True)


def lexical_threshold() -> float:
    return _env_float("Z_SKILL_LEXICAL_THRESHOLD", 0.28)


def chroma_weak_threshold() -> float:
    return _env_float("Z_SKILL_CHROMA_WEAK", 0.45)


def near_dup_title_threshold() -> float:
    return _env_float("Z_SKILL_NEAR_DUP_TITLE", 0.60)


def near_dup_symptom_threshold() -> float:
    return _env_float("Z_SKILL_NEAR_DUP_SYMPTOM", 0.55)


def tokenize_folded(text: str) -> Set[str]:
    """Tokenize and add stem-family ids alongside raw tokens."""
    raw = tokenize(text)
    out: Set[str] = set(raw)
    for tok in raw:
        fam = _STEM_FAMILIES.get(tok)
        if fam:
            out.add(f"fam:{fam}")
    # Phrase-ish: use-after-free in original text
    low = (text or "").lower()
    if "use-after-free" in low or "use after free" in low:
        out.add("fam:lifetime")
        out.add("uaf")
    return out


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / float(len(a | b))


def title_similarity(a: str, b: str) -> float:
    return jaccard(tokenize_folded(a), tokenize_folded(b))


def _entry_bug_blob(entry: SkillIndexEntry) -> str:
    return " ".join(
        [
            entry.title or "",
            entry.description or "",
            getattr(entry, "symptom_description", "") or "",
            getattr(entry, "root_cause_category", "") or "",
            getattr(entry, "fix_technique", "") or "",
            getattr(entry, "verification_method", "") or "",
            " ".join(entry.tags or []),
            " ".join(entry.triggers or []),
        ]
    )


def bug_field_score(task: str, entry: SkillIndexEntry) -> Tuple[float, str]:
    """
    Lexical score in [0, 1] using bug_pattern fields + stem folding.

    Returns (score, reason).
    """
    task_f = tokenize_folded(task)
    if not task_f:
        return 0.0, "empty task"

    title_f = tokenize_folded(entry.title or "")
    symptom_f = tokenize_folded(getattr(entry, "symptom_description", "") or "")
    desc_f = tokenize_folded(entry.description or "")
    tech_f = tokenize_folded(getattr(entry, "fix_technique", "") or "")
    cat = (getattr(entry, "root_cause_category", "") or "").strip().lower()
    cat_f = tokenize_folded(cat.replace("_", " ")) if cat else set()
    extra_f = tokenize_folded(
        " ".join(
            [
                " ".join(entry.tags or []),
                " ".join(entry.triggers or []),
                getattr(entry, "verification_method", "") or "",
            ]
        )
    )

    reasons: List[str] = []
    score = 0.0

    title_j = jaccard(task_f, title_f)
    if title_j:
        score += 0.35 * title_j
        if title_j >= 0.2:
            reasons.append(f"title={title_j:.2f}")

    symptom_j = jaccard(task_f, symptom_f)
    if symptom_j:
        score += 0.30 * symptom_j
        if symptom_j >= 0.2:
            reasons.append(f"symptom={symptom_j:.2f}")

    desc_j = jaccard(task_f, desc_f)
    if desc_j:
        score += 0.12 * desc_j

    tech_j = jaccard(task_f, tech_f)
    if tech_j:
        score += 0.10 * tech_j

    extra_j = jaccard(task_f, extra_f | cat_f)
    if extra_j:
        score += 0.08 * extra_j

    # Family overlap boost (lru↔lfu)
    fam_task = {t for t in task_f if t.startswith("fam:")}
    fam_entry = {
        t
        for t in (title_f | symptom_f | desc_f | tech_f | extra_f | cat_f)
        if t.startswith("fam:")
    }
    if fam_task & fam_entry:
        score = min(1.0, score + 0.18)
        reasons.append("family:" + ",".join(sorted(x[4:] for x in fam_task & fam_entry)))

    # Exact category id present as tokens in task (underscored)
    if cat:
        cat_tokens = set(cat.split("_")) | {cat}
        if cat_tokens & task_f or cat.replace("_", "") in "".join(sorted(task_f)):
            score = min(1.0, score + 0.12)
            reasons.append(f"category={cat}")
        # Also: category string Jaccard with folded task
        if jaccard(task_f, cat_f) >= 0.25:
            score = min(1.0, score + 0.08)

    # BUG_CONCEPTS symptom keyword boost
    try:
        from .bug_concepts import BUG_CONCEPTS

        low_task = (task or "").lower()
        for concept in BUG_CONCEPTS:
            if cat and concept.category_id == cat:
                hits = sum(1 for kw in concept.symptom_keywords if kw in low_task)
                if hits:
                    score = min(1.0, score + min(0.15, 0.05 * hits))
                    reasons.append(f"concept_kw={hits}")
                break
            # No category on entry — still boost if keywords + title family match
            if not cat:
                hits = sum(1 for kw in concept.symptom_keywords if kw in low_task)
                if hits >= 2 and fam_task & fam_entry:
                    score = min(1.0, score + 0.08)
    except Exception:
        pass

    score = min(1.0, score)
    reason = "; ".join(reasons) if reasons else f"score={score:.2f}"
    return score, reason


def lexical_match_skills(
    task: str,
    index: Sequence[SkillIndexEntry],
    *,
    kind: Optional[str] = None,
    threshold: Optional[float] = None,
    limit: int = 5,
) -> List[Tuple[SkillIndexEntry, float, str]]:
    """Return (entry, score, reason) above threshold, best first."""
    thr = lexical_threshold() if threshold is None else threshold
    scored: List[Tuple[SkillIndexEntry, float, str]] = []
    for entry in index:
        if kind and (entry.kind or "") != kind:
            continue
        score, reason = bug_field_score(task, entry)
        if score >= thr:
            scored.append((entry, score, reason))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


@dataclass
class NearDupMatch:
    skill: Skill
    score: float
    reason: str


def _skill_to_entry_like(skill: Skill) -> SkillIndexEntry:
    return SkillIndexEntry(
        id=skill.id or "",
        title=skill.title or "",
        description=skill.description or "",
        tags=list(skill.tags or []),
        triggers=list(skill.triggers or []),
        languages=list(skill.languages or []),
        kind=skill.kind or "",
        symptom_description=skill.symptom_description or "",
        root_cause_category=skill.root_cause_category or "",
        fix_technique=skill.fix_technique or "",
        verification_method=skill.verification_method or "",
        language=skill.language or "",
    )


def find_near_dup(
    new_skill: Skill,
    candidates: Sequence[Skill],
    *,
    title_threshold: Optional[float] = None,
    symptom_threshold: Optional[float] = None,
) -> Optional[NearDupMatch]:
    """
    Find best existing skill to merge into, or None.

    Prefer same root_cause_category + solid title/symptom overlap; else
    high title or symptom Jaccard with stem folding.
    """
    if not candidates:
        return None
    t_thr = near_dup_title_threshold() if title_threshold is None else title_threshold
    s_thr = (
        near_dup_symptom_threshold()
        if symptom_threshold is None
        else symptom_threshold
    )
    new_title = new_skill.title or ""
    new_sym = new_skill.symptom_description or new_skill.description or ""
    new_cat = (new_skill.root_cause_category or "").strip().lower()

    best: Optional[NearDupMatch] = None
    for other in candidates:
        if other.id and new_skill.id and other.id == new_skill.id:
            continue
        # Prefer same kind
        if (other.kind or "") != (new_skill.kind or "") and (
            new_skill.kind or ""
        ) == SKILL_KIND_BUG_PATTERN:
            if (other.kind or "") != SKILL_KIND_BUG_PATTERN:
                continue

        title_j = title_similarity(new_title, other.title or "")
        symptom_j = jaccard(
            tokenize_folded(new_sym),
            tokenize_folded(other.symptom_description or other.description or ""),
        )
        other_cat = (other.root_cause_category or "").strip().lower()
        same_cat = bool(new_cat and other_cat and new_cat == other_cat)

        score = 0.0
        reason = ""
        if same_cat and (title_j >= 0.45 or symptom_j >= 0.50):
            score = max(title_j, symptom_j) + 0.2
            reason = f"same_category={new_cat}; title={title_j:.2f}; symptom={symptom_j:.2f}"
        elif title_j >= t_thr:
            score = title_j
            reason = f"title={title_j:.2f}"
        elif symptom_j >= s_thr:
            score = symptom_j
            reason = f"symptom={symptom_j:.2f}"
        else:
            continue

        if best is None or score > best.score:
            best = NearDupMatch(skill=other, score=score, reason=reason)
    return best


def _append_unique_paragraph(existing: str, addition: str, *, heading: str) -> str:
    existing = (existing or "").rstrip()
    addition = (addition or "").strip()
    if not addition:
        return existing
    if addition in existing:
        return existing
    block = f"\n\n## {heading}\n\n{addition}\n"
    if not existing:
        return block.strip() + "\n"
    return existing + block


def merge_into_existing(
    existing: Skill,
    new_skill: Skill,
    *,
    grounding_note: str = "",
) -> Skill:
    """
    Keep existing id/path/title; append evidence from new_skill.

    Mutates and returns ``existing``.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if new_skill.root_cause_explanation:
        existing.root_cause_explanation = _append_unique_paragraph(
            existing.root_cause_explanation or "",
            new_skill.root_cause_explanation,
            heading=f"Additional root-cause notes ({stamp})",
        ).strip()
    if new_skill.fix_technique:
        existing.fix_technique = _append_unique_paragraph(
            existing.fix_technique or "",
            new_skill.fix_technique,
            heading=f"Additional fix technique ({stamp})",
        ).strip()
    if new_skill.verification_method:
        existing.verification_method = _append_unique_paragraph(
            existing.verification_method or "",
            new_skill.verification_method,
            heading=f"Additional verification ({stamp})",
        ).strip()
    if new_skill.symptom_description and not existing.symptom_description:
        existing.symptom_description = new_skill.symptom_description
    if new_skill.root_cause_category and not existing.root_cause_category:
        existing.root_cause_category = new_skill.root_cause_category

    # Unions
    def _union(a, b):
        out = []
        seen = set()
        for x in list(a or []) + list(b or []):
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    existing.tags = _union(existing.tags, new_skill.tags)
    existing.triggers = _union(existing.triggers, new_skill.triggers)
    existing.languages = _union(existing.languages, new_skill.languages)
    existing.source_files = _union(existing.source_files, new_skill.source_files)
    existing.grounded_symbols = _union(
        existing.grounded_symbols, new_skill.grounded_symbols
    )

    if new_skill.content:
        note = grounding_note.strip()
        body = new_skill.content.strip()
        if note:
            body = f"{note}\n\n{body}"
        existing.content = _append_unique_paragraph(
            existing.content or "",
            body[:4000],
            heading=f"Additional evidence (captured {stamp})",
        )

    # Stay draft if either side needs review
    if new_skill.needs_review or (new_skill.quality_state or "") == "draft":
        existing.needs_review = True
        if (existing.quality_state or "") == "verified":
            pass  # keep verified if already accepted
        else:
            existing.quality_state = "draft"
    existing.shared = bool(existing.shared or new_skill.shared)
    return existing


@dataclass
class RetrieveTrace:
    chroma_available: bool = False
    chroma_count: int = 0
    chroma_top: List[tuple] = field(default_factory=list)  # (id, title, score)
    chroma_kept: int = 0
    lexical_ran: bool = False
    lexical_top: List[tuple] = field(default_factory=list)  # (id, title, score, reason)
    merged_ids: List[str] = field(default_factory=list)
    skip_reasons: List[str] = field(default_factory=list)
    note: str = ""

    def format_lines(self, *, limit: int = 6) -> List[str]:
        lines = []
        parts = [
            f"chroma={'yes' if self.chroma_available else 'no'}",
            f"n={self.chroma_count}",
            f"kept={self.chroma_kept}",
        ]
        if self.chroma_top:
            top = self.chroma_top[0]
            parts.append(f"top={top[1][:40]!r}@{top[2]:.2f}")
        head = "Skill retrieve: " + " ".join(parts)
        if self.note:
            head += f" ({self.note})"
        lines.append(head)
        if self.lexical_ran:
            if self.lexical_top:
                lid, title, score, reason = self.lexical_top[0]
                lines.append(
                    f"Skill retrieve: lexical hit `{title}` score={score:.2f} ({reason})"
                )
            else:
                lines.append("Skill retrieve: lexical ran — no hits above threshold")
        if not self.merged_ids and not self.chroma_kept:
            lines.append(
                "Skill retrieve: no candidates (chroma empty/miss + lexical miss) "
                "— capability gaps may follow"
            )
        for sid in self.merged_ids[:limit]:
            lines.append(f"Skill retrieve: candidate id={sid[:8]}")
        return lines[: limit + 2]


_LAST_RETRIEVE_TRACE: Optional[RetrieveTrace] = None


def get_last_retrieve_trace() -> Optional[RetrieveTrace]:
    return _LAST_RETRIEVE_TRACE


def set_last_retrieve_trace(trace: Optional[RetrieveTrace]) -> None:
    global _LAST_RETRIEVE_TRACE
    _LAST_RETRIEVE_TRACE = trace
