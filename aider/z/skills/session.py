"""Session skill registry — discover, retrieve, route, multi-checkpoint inject."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

from .index import (
    entries_from_local_index,
    entries_from_remote_index,
    match_skills,
    merge_index,
)
from .remote import fetch_skill, fetch_skill_index
from .router import (
    artifacts_satisfied,
    collect_repo_signals,
    mark_skill_satisfied,
    route_skills,
    task_is_bugfix_intent,
)
from .schema import SKILL_KIND_BUG_PATTERN, Skill, SkillIndexEntry
from .store import LocalSkillStore
from .vector import get_skill_vector_index, upsert_skill_vector

_SESSION_INDEX: list[SkillIndexEntry] = []
# Skills already injected into the chat this session (dedupe across checkpoints)
_INJECTED_IDS: set[str] = set()


def get_session_skill_index() -> list[SkillIndexEntry]:
    return list(_SESSION_INDEX)


def get_injected_skill_ids() -> Set[str]:
    return set(_INJECTED_IDS)


def clear_session_skills() -> None:
    _SESSION_INDEX.clear()
    _INJECTED_IDS.clear()


def _sync_local_to_chroma(store: LocalSkillStore) -> None:
    try:
        index = get_skill_vector_index()
        if not index.available:
            return
        skills = store.list_skills()
        if not skills:
            return
        if index.count() == 0 or index.count() != len(skills):
            index.reindex(skills)
        else:
            for skill in skills:
                upsert_skill_vector(skill)
    except Exception:
        pass


def load_skills_for_session(io=None) -> list[SkillIndexEntry]:
    """
    Build a lightweight skill index at session start:
      - scan ~/.z/skills/ (metadata only)
      - upsert into ChromaDB
      - fetch workspace/personal index from z_server when signed in
    Full content is loaded later via metadata.path after a match.
    """
    global _SESSION_INDEX
    store = LocalSkillStore()
    local = entries_from_local_index(store.index())
    remote = entries_from_remote_index(fetch_skill_index())
    merged = merge_index(local, remote)
    _SESSION_INDEX = merged
    _INJECTED_IDS.clear()
    _sync_local_to_chroma(store)

    if io and merged:
        io.tool_output(f"Skills: {len(merged)} available")
    elif io:
        io.tool_output("Skills: none yet — paste with /skills add or z skill add")
    return merged


def resolve_full_skill(entry: SkillIndexEntry) -> Optional[Skill]:
    """Load full skill content via path, then local id, then remote API."""
    store = LocalSkillStore()

    if entry.path:
        skill = store.get_by_path(entry.path)
        if skill and skill.content:
            _copy_router_fields(entry, skill)
            return skill
        name = Path(entry.path).name
        candidate = store.root / name
        if candidate.is_file():
            skill = store.read_file(candidate)
            if skill and skill.content:
                _copy_router_fields(entry, skill)
                return skill

    local = store.get(entry.id)
    if local and local.content:
        _copy_router_fields(entry, local)
        return local
    if entry.filename:
        local = store.get(entry.filename.replace(".md", ""))
        if local and local.content:
            _copy_router_fields(entry, local)
            return local

    rid = entry.remote_id or (entry.id if entry.source == "remote" else None)
    if rid:
        raw = fetch_skill(rid)
        if raw:
            skill = Skill.from_dict(raw)
            _copy_router_fields(entry, skill)
            try:
                if not store.get(skill.id):
                    path = store.save(skill)
                    skill.path = str(path)
                    upsert_skill_vector(skill)
            except OSError:
                pass
            return skill
    return None


def _copy_router_fields(entry: SkillIndexEntry, skill: Skill) -> None:
    """Ensure router fields from the index entry win when body file is older."""
    if entry.kind:
        skill.kind = entry.kind
    if entry.languages:
        skill.languages = list(entry.languages)
    if entry.artifacts:
        skill.artifacts = list(entry.artifacts)
    skill.apply_once = bool(entry.apply_once)
    if entry.capability:
        skill.capability = entry.capability
    if entry.grounded_symbols:
        skill.grounded_symbols = list(entry.grounded_symbols)
    if entry.source_files:
        skill.source_files = list(entry.source_files)
    skill.needs_review = bool(entry.needs_review)
    if getattr(entry, "quality_state", None):
        skill.quality_state = entry.quality_state
    if getattr(entry, "repo_key", None):
        skill.repo_key = entry.repo_key
    if getattr(entry, "shared", False):
        skill.shared = True


def retrieve_skill_candidates(
    task: str,
    *,
    threshold: float = 0.40,
    limit: int = 5,
    max_distance: float = 0.55,
    kind: Optional[str] = None,
    pool: Optional[str] = None,
) -> List[Tuple[Skill, float]]:
    """
    First-stage retrieval only (Chroma / keywords / lexical fallback). Does not inject.
    Returns (skill, score) with higher score = better.

    For bug-fix tasks, pass ``kind="bug_pattern"`` / ``pool="bug_pattern"``
    to search that logical pool inside the same Chroma collection.
    """
    from .near_dup import (
        chroma_weak_threshold,
        get_last_retrieve_trace,
        lexical_fallback_enabled,
        lexical_match_skills,
        lexical_threshold,
        RetrieveTrace,
        set_last_retrieve_trace,
    )

    matches: list[tuple[SkillIndexEntry, float]] = []
    trace = RetrieveTrace()
    chroma_scores: list[float] = []

    try:
        vindex = get_skill_vector_index()
        trace.chroma_available = bool(vindex.available)
        if vindex.available:
            try:
                trace.chroma_count = int(vindex.count())
            except Exception:
                trace.chroma_count = 0
        if vindex.available and trace.chroma_count > 0:
            # Tighter than the old 0.85 — router still filters further
            # query() returns (entry, score) with score = 1 - cosine distance
            raw = list(
                vindex.query(
                    task,
                    k=limit,
                    max_distance=max_distance,
                    kind=kind,
                    pool=pool,
                    boost_bug_text=task
                    if (kind == SKILL_KIND_BUG_PATTERN or pool == "bug_pattern")
                    else None,
                )
            )
            for entry, score in raw:
                chroma_scores.append(float(score))
                trace.chroma_top.append(
                    (entry.id or "", entry.title or "", float(score))
                )
            matches = list(raw)
            trace.chroma_kept = len(matches)
    except Exception:
        matches = []
        trace.note = "chroma error"

    best_chroma = max(chroma_scores) if chroma_scores else 0.0
    weak = bool(matches) and best_chroma < chroma_weak_threshold()
    need_lexical = lexical_fallback_enabled() and (
        not matches or weak
    )
    if weak and matches:
        trace.note = (trace.note + "; " if trace.note else "") + "weak chroma"

    # Legacy keyword fallback when chroma empty and lexical off
    if not matches and not need_lexical:
        kw = match_skills(task, _SESSION_INDEX, threshold=threshold, limit=limit * 2)
        if kind:
            kw = [(e, s) for e, s in kw if (e.kind or "") == kind]
        matches = kw[:limit]

    if need_lexical:
        trace.lexical_ran = True
        lex = lexical_match_skills(
            task,
            _SESSION_INDEX,
            kind=kind,
            threshold=lexical_threshold(),
            limit=max(limit, 5),
        )
        for entry, score, reason in lex:
            trace.lexical_top.append(
                (entry.id or "", entry.title or "", float(score), reason)
            )
        # Merge by id — prefer higher score
        by_id: dict[str, tuple[SkillIndexEntry, float]] = {}
        for entry, score in matches:
            key = entry.id or entry.title or str(id(entry))
            by_id[key] = (entry, float(score))
        for entry, score, _reason in lex:
            key = entry.id or entry.title or str(id(entry))
            prev = by_id.get(key)
            if prev is None or float(score) > prev[1]:
                by_id[key] = (entry, float(score))
        matches = sorted(by_id.values(), key=lambda x: x[1], reverse=True)[:limit]
        if not lex and not matches:
            # Also try keyword as last resort
            kw = match_skills(task, _SESSION_INDEX, threshold=threshold, limit=limit * 2)
            if kind:
                kw = [(e, s) for e, s in kw if (e.kind or "") == kind]
            matches = kw[:limit]

    out: List[Tuple[Skill, float]] = []
    for entry, score in matches:
        skill = resolve_full_skill(entry)
        if not skill:
            continue
        # Copy bug_pattern fields from index entry when the on-disk skill is thin
        if entry.symptom_description and not skill.symptom_description:
            skill.symptom_description = entry.symptom_description
        if entry.root_cause_category and not skill.root_cause_category:
            skill.root_cause_category = entry.root_cause_category
        if entry.fix_technique and not skill.fix_technique:
            skill.fix_technique = entry.fix_technique
        if entry.verification_method and not skill.verification_method:
            skill.verification_method = entry.verification_method
        if entry.language and not skill.language:
            skill.language = entry.language
        out.append((skill, float(score)))
        if skill.id:
            trace.merged_ids.append(skill.id)
        if len(out) >= limit:
            break
    set_last_retrieve_trace(trace)
    return out


def select_relevant_skills(
    task: str,
    *,
    threshold: float = 0.40,
    limit: int = 2,
    root: Optional[Path] = None,
    already_injected: Optional[Set[str]] = None,
) -> List[Skill]:
    """
    Retrieve + route for the current workflow checkpoint.

    Unlike the old unconditional inject, this applies the skill router so
    scaffolds/wrong-stack skills are skipped, and already-injected skills
    are not re-applied.
    """
    injected = already_injected if already_injected is not None else _INJECTED_IDS
    candidates = retrieve_skill_candidates(task, threshold=threshold, limit=max(limit, 5))
    approved, _decisions = route_skills(
        task,
        candidates,
        root=root,
        already_injected=injected,
        limit=limit,
        min_score=threshold,
    )
    return approved


def pull_skills_for_checkpoint(
    task: str,
    *,
    root: Optional[Path] = None,
    limit: int = 2,
    checkpoint: str = "turn",
) -> Tuple[List[Skill], List[str]]:
    """
    Multi-checkpoint helper: route skills for this step, mark injected,
    and refresh scaffold satisfaction from the live tree.

    Returns (skills_to_inject_now, skip_reasons for verbose UI).
    """
    root_path = Path(root or Path.cwd())
    # Update satisfaction for any scaffold whose artifacts now exist
    try:
        for entry in _SESSION_INDEX:
            if (entry.kind or "") != "scaffold" and not entry.apply_once:
                continue
            if not entry.id:
                continue
            if artifacts_satisfied(root_path, entry.artifacts):
                mark_skill_satisfied(root_path, entry.id)
    except Exception:
        pass

    bugfix = task_is_bugfix_intent(task)
    if bugfix:
        # Search the bug_pattern pool first, then fall back to feature skills
        pattern_cands = retrieve_skill_candidates(
            task,
            limit=max(limit, 3),
            kind=SKILL_KIND_BUG_PATTERN,
            pool="bug_pattern",
        )
        feature_cands = retrieve_skill_candidates(task, limit=max(limit, 3))
        # Dedup by id, patterns first
        seen: set[str] = set()
        candidates: List[Tuple[Skill, float]] = []
        for skill, score in pattern_cands + feature_cands:
            sid = skill.id or ""
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            candidates.append((skill, score))
    else:
        candidates = retrieve_skill_candidates(task, limit=max(limit, 5))

    approved, decisions = route_skills(
        task,
        candidates,
        root=root_path,
        already_injected=_INJECTED_IDS,
        limit=limit,
    )
    skip_reasons = [
        f"{d.skill.title}: {d.reason}" for d in decisions if not d.apply
    ]
    try:
        from .near_dup import get_last_retrieve_trace, set_last_retrieve_trace

        tr = get_last_retrieve_trace()
        if tr is not None:
            tr.skip_reasons = list(skip_reasons)
            set_last_retrieve_trace(tr)
    except Exception:
        pass

    for skill in approved:
        if skill.id:
            _INJECTED_IDS.add(skill.id)
        # After injecting a scaffold, if artifacts already exist, mark satisfied
        if ((skill.kind or "") == "scaffold" or skill.apply_once) and artifacts_satisfied(
            root_path, skill.artifacts
        ):
            mark_skill_satisfied(root_path, skill.id)

    return approved, skip_reasons


def note_scaffold_progress(root: Optional[Path] = None) -> None:
    """Call after edits settle — mark scaffolds satisfied when artifacts appear."""
    root_path = Path(root or Path.cwd())
    for entry in _SESSION_INDEX:
        if (entry.kind or "") != "scaffold" and not entry.apply_once:
            continue
        if entry.id and artifacts_satisfied(root_path, entry.artifacts):
            mark_skill_satisfied(root_path, entry.id)


def format_bug_pattern_hypothesis(skill: Skill, *, repo_language: str = "") -> str:
    """Labeled hypothesis — not an auto-applied playbook."""
    from .bug_concepts import language_note

    lang = (
        (skill.language or "").strip().lower()
        or ((skill.languages or [""])[0] if skill.languages else "")
        or (repo_language or "").strip().lower()
    )
    note = language_note(skill.root_cause_category, lang) if skill.root_cause_category else None
    symptom = skill.symptom_description or skill.description or skill.title
    lines = [
        "### Previously-solved bug hypothesis (not auto-applied)",
        f"This resembles a previously-solved bug: {symptom}",
    ]
    if skill.root_cause_category:
        lines.append(f"→ root_cause_category: `{skill.root_cause_category}`")
    if skill.root_cause_explanation:
        lines.append(f"Diagnosis: {skill.root_cause_explanation}")
    if skill.fix_technique:
        lines.append(f"Fix technique: {skill.fix_technique}")
    if skill.verification_method:
        lines.append(f"Verification then: {skill.verification_method}")
    if note:
        lines.append(f"In this language ({lang or 'unknown'}): {note}")
    lines.append(
        "Treat this as a strong starting hypothesis to accelerate diagnosis — "
        "verify against the current codebase before applying."
    )
    return "\n".join(lines)


def format_skills_for_context(skills: Sequence[Skill], *, checkpoint: str = "turn") -> str:
    """
    Format matched skills for injection into the coding turn.

    Default (OpenCode-inspired): compact *directives* — title/meta + truncated
    body — so playbooks steer without crowding the coder context. Full bodies
    remain on disk at each skill's path.

    Set ``Z_SKILL_INJECT_FULL=1`` to restore legacy full-markdown injection.
    """
    if not skills:
        return ""

    from aider.z.coding_context import format_skills_compact, skill_inject_full_enabled

    if not skill_inject_full_enabled():
        return format_skills_compact(skills, checkpoint=checkpoint)

    patterns = [s for s in skills if (s.kind or "") == SKILL_KIND_BUG_PATTERN]
    others = [s for s in skills if (s.kind or "") != SKILL_KIND_BUG_PATTERN]
    parts: List[str] = []
    if patterns:
        parts.append(
            "Bug-pattern matches for this task (hypotheses — do not auto-apply):"
        )
        parts.append("")
        for s in patterns:
            parts.append(format_bug_pattern_hypothesis(s))
            parts.append("")
    if not others:
        return "\n".join(parts).strip() + "\n"
    if checkpoint == "reflect":
        header = (
            "A new step in the workflow needs these reusable skills. "
            "Follow them for this step:"
        )
    else:
        header = (
            "The following reusable skills matched this task. Follow them where relevant:"
        )
    parts.append(header)
    parts.append("")
    for s in others:
        kind = s.kind or "playbook"
        parts.append(f"### Skill: {s.title} [{kind}]")
        if s.description:
            parts.append(f"_{s.description}_")
        parts.append("")
        parts.append(s.content.strip())
        parts.append("")
    return "\n".join(parts).strip()


def format_skill_metadata(skill: Skill) -> str:
    meta = skill.metadata_public()
    lines = [
        f"Skill: {meta['title']}",
        f"  description: {meta['description']}",
        f"  kind: {meta.get('kind')}",
        f"  languages: {', '.join(meta.get('languages') or []) or '(none)'}",
        f"  artifacts: {', '.join(meta.get('artifacts') or []) or '(none)'}",
        f"  capability: {meta.get('capability') or '(none)'}",
        f"  grounded_symbols: {', '.join(meta.get('grounded_symbols') or []) or '(none)'}",
        f"  source_files: {', '.join(meta.get('source_files') or []) or '(none)'}",
        f"  needs_review: {'yes' if meta.get('needs_review') else 'no'}",
        f"  quality_state: {meta.get('quality_state') or 'verified'}",
        f"  tags: {', '.join(meta['tags']) or '(none)'}",
        f"  triggers: {', '.join(meta['triggers']) or '(none)'}",
        f"  project_types: {', '.join(meta['project_types']) or '(none)'}",
        f"  path: {meta['path'] or '(unknown)'}",
        f"  source: {meta['source']}",
        f"  scope: {meta['scope']}",
        (
            "  repo: shared (all projects)"
            if meta.get("shared")
            else f"  repo: {meta.get('repo_key') or '(unscoped legacy)'}"
        ),
    ]
    if (meta.get("kind") or "") == SKILL_KIND_BUG_PATTERN:
        lines.extend(
            [
                f"  symptom: {meta.get('symptom_description') or '(none)'}",
                f"  root_cause_category: {meta.get('root_cause_category') or '(none)'}",
                f"  fix_technique: {meta.get('fix_technique') or '(none)'}",
                f"  verification: {meta.get('verification_method') or '(none)'}",
                f"  language: {meta.get('language') or '(none)'}",
            ]
        )
    return "\n".join(lines)


def print_skills_list(io) -> None:
    store = LocalSkillStore()
    local = store.list_skills()
    remote_rows = fetch_skill_index()

    if not local and not remote_rows:
        io.tool_output("No skills yet.")
        io.tool_output("Paste one with: /skills add")
        io.tool_output('Or generate: z skill create "how this repo handles …"')
        return

    if local:
        io.tool_output(f"Local skills (~/.z/skills/) — {len(local)}:")
        for s in local:
            kind = s.kind or "playbook"
            langs = ",".join(s.languages or []) or "-"
            review = " needs-review" if s.needs_review else ""
            qstate = (s.quality_state or "verified").strip()
            if qstate == "draft":
                review = (review + " draft").strip()
            elif qstate == "rejected":
                review = (review + " rejected").strip()
            if s.shared:
                repo_tag = " shared"
            elif s.repo_key:
                repo_tag = " repo-bound"
            else:
                repo_tag = ""
            io.tool_output(f"  • {s.title}  [{kind}/{langs}]{review}{repo_tag}")
            if s.description:
                io.tool_output(f"      {s.description}")
            if s.path:
                io.tool_output(f"      {s.path}")
    if remote_rows:
        io.tool_output("")
        io.tool_output(f"Workspace / account skills — {len(remote_rows)}:")
        for r in remote_rows:
            scope = r.get("scope") or "personal"
            io.tool_output(f"  • {r.get('title')}  [{scope}]")
            if r.get("description"):
                io.tool_output(f"      {r['description']}")
