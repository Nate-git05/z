"""Taxonomy blind-spot learning — notice evidence_regex gaps; never auto-edit them.

When a known ``root_cause_category`` fails grounding because the diff lacks
regex evidence, we append a miss record. If a human later ``z skill accept``s
that draft, candidate terms from the added diff are counted. ``z taxonomy
review`` surfaces terms that recur across independently confirmed skills so a
human can patch ``bug_concepts.py`` via normal review — the running agent
never rewrites its own verification rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from aider.z.paths import ensure_z_home

from .grounding import extract_call_site_names


def _skills_data_dir() -> Path:
    d = ensure_z_home() / "skills"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def grounding_misses_path() -> Path:
    return _skills_data_dir() / "grounding_misses.jsonl"


def taxonomy_candidates_path() -> Path:
    return _skills_data_dir() / "taxonomy_candidates.json"


@dataclass
class CandidateTerm:
    term: str
    count: int
    skill_ids: List[str] = field(default_factory=list)
    skill_titles: List[str] = field(default_factory=list)


def record_grounding_miss(
    category_id: str,
    added_diff_blob: str,
    skill_id: str,
    *,
    skill_title: str = "",
) -> None:
    """Append to skills/grounding_misses.jsonl. Write-only — no gate behavior change."""
    cat = (category_id or "").strip()
    sid = (skill_id or "").strip()
    if not cat or not sid:
        return
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "category_id": cat,
        "skill_id": sid,
        "skill_title": (skill_title or "").strip(),
        "added_diff_blob": added_diff_blob or "",
    }
    path = grounding_misses_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def latest_miss_for_skill(skill_id: str) -> Optional[dict[str, Any]]:
    """Most recent grounding-miss row for *skill_id*, or None."""
    sid = (skill_id or "").strip()
    if not sid:
        return None
    path = grounding_misses_path()
    if not path.is_file():
        return None
    latest: Optional[dict[str, Any]] = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("skill_id") or "") == sid:
                latest = row
    except OSError:
        return None
    return latest


def _load_candidates() -> dict[str, Any]:
    path = taxonomy_candidates_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_candidates(data: dict[str, Any]) -> None:
    path = taxonomy_candidates_path()
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _term_already_evidenced(category_id: str, term: str) -> bool:
    """True when the category's current evidence_regex already covers *term*."""
    from .bug_concepts import concept_by_id

    concept = concept_by_id(category_id)
    if not concept or not term:
        return False
    rx = concept.evidence_regex
    if rx.search(term):
        return True
    if rx.search(f".{term}("):
        return True
    if rx.search(f"{term}("):
        return True
    return False


def candidate_terms_from_blob(category_id: str, added_diff_blob: str) -> List[str]:
    """Call-site names in the blob not already matched by the category regex."""
    terms: List[str] = []
    for name in extract_call_site_names(added_diff_blob or ""):
        if _term_already_evidenced(category_id, name):
            continue
        if name not in terms:
            terms.append(name)
    return terms


def record_confirmation_candidate(
    category_id: str,
    added_diff_blob: str,
    skill_id: str,
    *,
    skill_title: str = "",
) -> List[str]:
    """Count new candidate terms after a human accepts a grounding-miss draft.

    Only increments once per (category, term, skill_id) — repetition across
    independent skills, not repeated mentions inside one diff.
    Returns the terms newly counted for this skill (may be empty).
    """
    cat = (category_id or "").strip()
    sid = (skill_id or "").strip()
    if not cat or not sid:
        return []
    terms = candidate_terms_from_blob(cat, added_diff_blob or "")
    if not terms:
        return []
    data = _load_candidates()
    cat_bucket = data.setdefault(cat, {})
    if not isinstance(cat_bucket, dict):
        cat_bucket = {}
        data[cat] = cat_bucket
    recorded: List[str] = []
    title = (skill_title or "").strip()
    for term in terms:
        entry = cat_bucket.get(term)
        if not isinstance(entry, dict):
            entry = {"count": 0, "skills": []}
        skills = entry.get("skills")
        if not isinstance(skills, list):
            skills = []
        existing_ids = {
            str(s.get("id") or "")
            for s in skills
            if isinstance(s, dict)
        }
        if sid in existing_ids:
            cat_bucket[term] = entry
            continue
        skills.append({"id": sid, "title": title})
        entry["skills"] = skills
        entry["count"] = len(skills)
        cat_bucket[term] = entry
        recorded.append(term)
    _save_candidates(data)
    return recorded


def list_candidates(min_count: int = 2) -> Dict[str, List[CandidateTerm]]:
    """Candidates recurring across ≥ ``min_count`` independently confirmed skills."""
    data = _load_candidates()
    out: Dict[str, List[CandidateTerm]] = {}
    for cat, bucket in data.items():
        if not isinstance(bucket, dict):
            continue
        rows: List[CandidateTerm] = []
        for term, entry in bucket.items():
            if not isinstance(entry, dict):
                continue
            skills = entry.get("skills") or []
            if not isinstance(skills, list):
                skills = []
            ids: List[str] = []
            titles: List[str] = []
            seen: set[str] = set()
            for s in skills:
                if not isinstance(s, dict):
                    continue
                sid = str(s.get("id") or "").strip()
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                ids.append(sid)
                titles.append(str(s.get("title") or "").strip() or sid)
            count = len(ids)
            if count < int(min_count or 0):
                continue
            rows.append(
                CandidateTerm(
                    term=str(term),
                    count=count,
                    skill_ids=ids,
                    skill_titles=titles,
                )
            )
        rows.sort(key=lambda r: (-r.count, r.term))
        if rows:
            out[str(cat)] = rows
    return out


def format_taxonomy_review(min_count: int = 2) -> str:
    """Human-readable report for ``z taxonomy review`` (read-only)."""
    by_cat = list_candidates(min_count=min_count)
    if not by_cat:
        return (
            f"No taxonomy candidates with ≥{min_count} independently confirmed "
            "skills yet.\n"
            "Accept draft bug_pattern skills that failed evidence grounding; "
            "recurring call-site terms will show up here for human review of "
            "bug_concepts.py — Z never edits that file itself."
        )
    lines: List[str] = [
        "Taxonomy blind-spot candidates (read-only — edit bug_concepts.py by hand):",
        "",
    ]
    for cat in sorted(by_cat):
        lines.append(f"[{cat}]")
        for row in by_cat[cat]:
            support = ", ".join(
                f"{t or sid} ({sid[:8]})"
                for sid, t in zip(row.skill_ids, row.skill_titles)
            )
            lines.append(
                f"  - {row.term}  ×{row.count}  ← {support}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
