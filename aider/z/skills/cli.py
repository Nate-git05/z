"""CLI helpers for z skill create / list."""

from __future__ import annotations

from typing import Optional

from .generate import generate_skill
from .remote import sync_skill
from .session import print_skills_list
from .store import LocalSkillStore


def cmd_skill_create(io, topic: str = "", *, model_name: Optional[str] = None, sync: bool = True) -> int:
    """Prompt (or use arg) for skill topic, generate via BYOK model, store locally (+ optional sync)."""
    topic = (topic or "").strip()
    if not topic:
        topic = io.prompt_ask("What should this skill cover?").strip()
    if not topic:
        io.tool_error("A skill description is required.")
        return 1

    io.tool_output("Generating skill with your connected model…")
    created_by = None
    try:
        from aider.z.auth import current_session

        creds = current_session()
        if creds:
            created_by = creds.display_name()
    except Exception:
        pass

    skill, err = generate_skill(topic, model_name=model_name, created_by=created_by)
    if err or not skill:
        io.tool_error(err or "Skill generation failed.")
        return 1

    store = LocalSkillStore()
    path = store.save(skill)
    io.tool_output(f"Saved skill: {skill.title}")
    io.tool_output(f"  {skill.description}")
    io.tool_output(f"  → {path}")

    if sync:
        remote_id = sync_skill(skill)
        if remote_id:
            skill.remote_id = remote_id
            store.save(skill)
            io.tool_output("  Synced to workspace/account (manage at /app/skills).")
        else:
            try:
                from aider.z.auth import current_session

                if current_session():
                    io.tool_warning("Could not sync to server; skill kept locally.")
            except Exception:
                pass

    return 0


def cmd_skill_list(io) -> int:
    print_skills_list(io)
    return 0


def save_skill_from_task(
    io,
    topic: str,
    *,
    context: str = "",
    model_name: Optional[str] = None,
) -> bool:
    """Used when the agent suggests saving a skill after a non-trivial task."""
    created_by = None
    try:
        from aider.z.auth import current_session

        creds = current_session()
        if creds:
            created_by = creds.display_name()
    except Exception:
        pass

    io.tool_output("Generating skill…")
    skill, err = generate_skill(
        topic, model_name=model_name, context=context, created_by=created_by
    )
    if err or not skill:
        io.tool_error(err or "Skill generation failed.")
        return False

    store = LocalSkillStore()
    path = store.save(skill)
    remote_id = sync_skill(skill)
    if remote_id:
        skill.remote_id = remote_id
        store.save(skill)
    io.tool_output(f"Saved skill “{skill.title}” → {path}")
    return True
