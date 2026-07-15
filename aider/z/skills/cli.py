"""CLI helpers for z skill add / create / list / show / reindex."""

from __future__ import annotations

from typing import Optional, Tuple

from .generate import generate_skill
from .infer import apply_inferred_metadata
from .remote import sync_skill
from .schema import Skill
from .session import format_skill_metadata, print_skills_list
from .store import LocalSkillStore
from .vector import get_skill_vector_index, upsert_skill_vector


def _created_by() -> Optional[str]:
    try:
        from aider.z.auth import current_session

        creds = current_session()
        if creds:
            return creds.display_name()
    except Exception:
        pass
    return None


def _persist_skill(io, skill: Skill, *, sync: bool = True) -> Skill:
    store = LocalSkillStore()
    apply_inferred_metadata(skill, source=skill.source)
    path = store.save(skill)
    skill.path = str(path)
    # Rewrite frontmatter with final path
    store.save(skill)
    upsert_skill_vector(skill)
    io.tool_output(f"Saved skill: {skill.title}")
    io.tool_output(f"  → {skill.path}")

    if sync:
        remote_id = sync_skill(skill)
        if remote_id:
            skill.remote_id = remote_id
            store.save(skill)
            upsert_skill_vector(skill)
            io.tool_output("  Synced to workspace/account (manage at /app/skills).")
        else:
            try:
                from aider.z.auth import current_session

                if current_session():
                    io.tool_warning("Could not sync to server; skill kept locally.")
            except Exception:
                pass
    return skill


def cmd_skill_add(io, content: str = "", *, sync: bool = True) -> int:
    """Paste/import a skill body. Z infers metadata and indexes in ChromaDB."""
    content = (content or "").strip()
    if not content:
        io.tool_output("Paste the skill markdown, then finish with a line containing only END")
        io.tool_output("(or press Enter on an empty prompt to cancel).")
        lines = []
        while True:
            line = io.prompt_ask("")
            if line is None:
                break
            if line.strip() == "END":
                break
            if not line.strip() and not lines:
                break
            lines.append(line)
        content = "\n".join(lines).strip()

    if not content:
        io.tool_error("No skill content provided.")
        return 1

    # Allow pasting a full file with frontmatter
    from .store import skill_from_markdown

    parsed = skill_from_markdown(content)
    kwargs = dict(
        title=parsed.title if parsed.title != "Untitled skill" else "",
        description=parsed.description,
        content=parsed.content or content,
        tags=list(parsed.tags or []),
        project_types=list(parsed.project_types or []),
        triggers=list(parsed.triggers or []),
        source="paste",
        created_by=_created_by(),
    )
    if parsed.id:
        kwargs["id"] = parsed.id
    skill = Skill(**kwargs)

    _persist_skill(io, skill, sync=sync)
    return 0


def cmd_skill_create(
    io, topic: str = "", *, model_name: Optional[str] = None, sync: bool = True
) -> int:
    """Generate a skill from a prompt via BYOK model."""
    topic = (topic or "").strip()
    if not topic:
        topic = io.prompt_ask("What should this skill cover?").strip()
    if not topic:
        io.tool_error("A skill description is required.")
        return 1

    io.tool_output("Generating skill with your connected model…")
    skill, err = generate_skill(topic, model_name=model_name, created_by=_created_by())
    if err or not skill:
        io.tool_error(err or "Skill generation failed.")
        return 1

    skill.source = "generate"
    _persist_skill(io, skill, sync=sync)
    return 0


def cmd_skill_list(io) -> int:
    print_skills_list(io)
    return 0


def cmd_skill_show(io, name: str = "") -> int:
    """Show skill metadata (and optionally full body)."""
    name = (name or "").strip()
    if not name:
        name = io.prompt_ask("Skill name or id").strip()
    if not name:
        io.tool_error("Skill name or id required.")
        return 1

    store = LocalSkillStore()
    skill = store.get(name)
    if not skill:
        # fuzzy title contains
        matches = [s for s in store.list_skills() if name.lower() in s.title.lower()]
        if len(matches) == 1:
            skill = matches[0]
        elif len(matches) > 1:
            io.tool_output("Multiple matches:")
            for s in matches[:10]:
                io.tool_output(f"  • {s.title} ({s.id[:8]})")
            return 1
    if not skill:
        io.tool_error(f"No skill found for “{name}”.")
        return 1

    io.tool_output(format_skill_metadata(skill))
    if io.confirm_ask("Open full skill body?", default="n"):
        io.tool_output("")
        io.tool_output(skill.content.strip())
    return 0


def cmd_skill_reindex(io) -> int:
    store = LocalSkillStore()
    skills = store.list_skills()
    try:
        index = get_skill_vector_index()
        if not index.available:
            io.tool_error("chromadb is not installed. pip install chromadb")
            return 1
        n = index.reindex(skills)
        io.tool_output(f"Reindexed {n} skill(s) into ChromaDB.")
        return 0
    except Exception as err:
        io.tool_error(f"Reindex failed: {err}")
        return 1


def save_skill_from_task(
    io,
    topic: str,
    *,
    context: str = "",
    model_name: Optional[str] = None,
) -> Tuple[Optional[Skill], bool]:
    """
    Capture a skill after a completed task.
    Returns (skill, created).
    Caller handles the "want to see metadata?" prompt.
    """
    io.tool_output("Generating skill…")
    skill, err = generate_skill(
        topic, model_name=model_name, context=context, created_by=_created_by()
    )
    if err or not skill:
        io.tool_error(err or "Skill generation failed.")
        return None, False

    skill.source = "capture"
    skill = _persist_skill(io, skill, sync=True)
    return skill, True


def offer_view_new_skill(io, skill: Skill) -> None:
    """Ask whether to show metadata for a newly captured skill."""
    if not io.confirm_ask("Want to see the new skill?", default="n"):
        return
    io.tool_output("")
    io.tool_output(format_skill_metadata(skill))
