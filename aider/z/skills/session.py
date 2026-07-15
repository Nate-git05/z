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
)
from .schema import Skill, SkillIndexEntry
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


def retrieve_skill_candidates(
    task: str,
    *,
    threshold: float = 0.40,
    limit: int = 5,
    max_distance: float = 0.55,
) -> List[Tuple[Skill, float]]:
    """
    First-stage retrieval only (Chroma / keywords). Does not inject.
    Returns (skill, score) with higher score = better.
    """
    matches: list[tuple[SkillIndexEntry, float]] = []

    try:
        vindex = get_skill_vector_index()
        if vindex.available and vindex.count() > 0:
            # Tighter than the old 0.85 — router still filters further
            # query() returns (entry, score) with score = 1 - cosine distance
            matches = list(vindex.query(task, k=limit, max_distance=max_distance))
    except Exception:
        matches = []

    if not matches:
        matches = match_skills(task, _SESSION_INDEX, threshold=threshold, limit=limit)

    out: List[Tuple[Skill, float]] = []
    for entry, score in matches:
        skill = resolve_full_skill(entry)
        if not skill:
            continue
        out.append((skill, float(score)))
        if len(out) >= limit:
            break
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


def format_skills_for_context(skills: Sequence[Skill], *, checkpoint: str = "turn") -> str:
    if not skills:
        return ""
    if checkpoint == "reflect":
        header = (
            "A new step in the workflow needs these reusable skills. "
            "Follow them for this step:"
        )
    else:
        header = (
            "The following reusable skills matched this task. Follow them where relevant:"
        )
    parts = [header, ""]
    for s in skills:
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
        f"  tags: {', '.join(meta['tags']) or '(none)'}",
        f"  triggers: {', '.join(meta['triggers']) or '(none)'}",
        f"  project_types: {', '.join(meta['project_types']) or '(none)'}",
        f"  path: {meta['path'] or '(unknown)'}",
        f"  source: {meta['source']}",
        f"  scope: {meta['scope']}",
    ]
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
            io.tool_output(f"  • {s.title}  [{kind}/{langs}]")
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
