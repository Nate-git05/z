"""Session skill registry — auto-discover at startup like MCP tools."""

from __future__ import annotations

from typing import List, Optional, Sequence

from .index import (
    entries_from_local_index,
    entries_from_remote_index,
    match_skills,
    merge_index,
)
from .remote import fetch_skill, fetch_skill_index
from .schema import Skill, SkillIndexEntry
from .store import LocalSkillStore

# Process-wide session registry (mirrors mcp_client._SESSION_TOOLS)
_SESSION_INDEX: list[SkillIndexEntry] = []
_PULLED_IDS: set[str] = set()


def get_session_skill_index() -> list[SkillIndexEntry]:
    return list(_SESSION_INDEX)


def clear_session_skills() -> None:
    _SESSION_INDEX.clear()
    _PULLED_IDS.clear()


def load_skills_for_session(io=None) -> list[SkillIndexEntry]:
    """
    Build a lightweight skill index at session start:
      - scan ~/.z/skills/ (title/description only)
      - fetch workspace/personal index from z_server when signed in
    Full content is NOT loaded until a task matches.
    """
    global _SESSION_INDEX
    store = LocalSkillStore()
    local = entries_from_local_index(store.index())
    remote = entries_from_remote_index(fetch_skill_index())
    merged = merge_index(local, remote)
    _SESSION_INDEX = merged
    _PULLED_IDS.clear()

    if io and merged:
        io.tool_output(f"Skills: {len(merged)} available (auto-matched by task)")
    return merged


def resolve_full_skill(entry: SkillIndexEntry) -> Optional[Skill]:
    """Load full skill content from local disk or remote API."""
    store = LocalSkillStore()
    local = store.get(entry.id)
    if local and local.content:
        return local
    if entry.filename:
        local = store.get(entry.filename.replace(".md", ""))
        if local and local.content:
            return local
    rid = entry.remote_id or (entry.id if entry.source == "remote" else None)
    if rid:
        raw = fetch_skill(rid)
        if raw:
            skill = Skill.from_dict(raw)
            # Cache locally for offline reuse
            try:
                if not store.get(skill.id):
                    store.save(skill)
            except OSError:
                pass
            return skill
    return None


def select_relevant_skills(
    task: str,
    *,
    threshold: float = 0.35,
    limit: int = 3,
) -> List[Skill]:
    """Match task text against the session index and return full skill bodies."""
    matches = match_skills(task, _SESSION_INDEX, threshold=threshold, limit=limit)
    skills: List[Skill] = []
    for entry, _score in matches:
        if entry.id in _PULLED_IDS:
            # Still include content if already pulled this session
            skill = resolve_full_skill(entry)
            if skill:
                skills.append(skill)
            continue
        skill = resolve_full_skill(entry)
        if skill:
            _PULLED_IDS.add(entry.id)
            skills.append(skill)
    return skills


def format_skills_for_context(skills: Sequence[Skill]) -> str:
    if not skills:
        return ""
    parts = [
        "The following reusable skills matched this task. Follow them where relevant:",
        "",
    ]
    for s in skills:
        parts.append(f"### Skill: {s.title}")
        if s.description:
            parts.append(f"_{s.description}_")
        parts.append("")
        parts.append(s.content.strip())
        parts.append("")
    return "\n".join(parts).strip()


def print_skills_list(io) -> None:
    store = LocalSkillStore()
    local = store.list_skills()
    remote_rows = fetch_skill_index()

    if not local and not remote_rows:
        io.tool_output("No skills yet.")
        io.tool_output("Create one with: z skill create \"how this repo handles …\"")
        return

    if local:
        io.tool_output(f"Local skills (~/.z/skills/) — {len(local)}:")
        for s in local:
            io.tool_output(f"  • {s.title}")
            if s.description:
                io.tool_output(f"      {s.description}")
    if remote_rows:
        io.tool_output("")
        io.tool_output(f"Workspace / account skills — {len(remote_rows)}:")
        for r in remote_rows:
            scope = r.get("scope") or "personal"
            io.tool_output(f"  • {r.get('title')}  [{scope}]")
            if r.get("description"):
                io.tool_output(f"      {r['description']}")
