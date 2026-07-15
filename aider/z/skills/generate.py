"""Generate skill content via the user's connected (BYOK) model."""

from __future__ import annotations

import json
import re
from typing import Optional, Tuple

from .infer import apply_inferred_metadata
from .schema import Skill, _as_str_list


SKILL_SYSTEM = """You generate reusable coding-agent skills.
A skill is a short, concrete instructions file describing how to do a specific
task or pattern in a codebase (conventions, steps, pitfalls, examples).

Respond with ONLY a JSON object (no markdown fences) with keys:
  "title": short plain-language title (max ~80 chars)
  "description": one sentence describing when to apply this skill
  "content": markdown body with clear steps, conventions, and examples
  "tags": optional array of short keywords
  "triggers": optional array of words/phrases that should activate this skill
  "project_types": optional array from [api, backend, frontend, mobile, infra, data, general]
"""


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    # Strip optional fences
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find first { ... }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def resolve_model(model_name: Optional[str] = None):
    """Resolve the user's BYOK model (same selection path as the agent)."""
    from aider.models import Model

    name = model_name
    if not name:
        # Prefer env override used by aider, else default
        import os

        name = os.environ.get("AIDER_MODEL") or os.environ.get("Z_MODEL")
    if not name:
        from aider.models import DEFAULT_MODEL_NAME

        name = DEFAULT_MODEL_NAME
    return Model(name)


def generate_skill(
    topic: str,
    *,
    model_name: Optional[str] = None,
    context: str = "",
    created_by: Optional[str] = None,
) -> Tuple[Optional[Skill], Optional[str]]:
    """
    Ask the connected model to write a skill for `topic`.
    Returns (skill, error_message).
    """
    topic = (topic or "").strip()
    if not topic:
        return None, "Describe what the skill should cover."

    try:
        model = resolve_model(model_name)
    except Exception as err:
        return None, f"Could not load model: {err}"

    user_content = f"Create a skill covering:\n{topic}\n"
    if context:
        user_content += f"\nAdditional context from the recent task:\n{context[:6000]}\n"

    messages = [
        {"role": "system", "content": SKILL_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    try:
        raw = model.simple_send_with_retries(messages)
    except Exception as err:
        return None, f"Model call failed: {err}"

    if not raw:
        return None, "Model returned an empty response. Check your API key / model."

    data = _extract_json(raw)
    if not data:
        # Fallback: treat whole response as content
        title = topic[:80]
        return (
            Skill(
                title=title,
                description=f"Skill generated for: {topic[:120]}",
                content=raw.strip(),
                created_by=created_by,
            ),
            None,
        )

    title = (data.get("title") or topic)[:120].strip()
    description = (data.get("description") or topic)[:400].strip()
    content = (data.get("content") or "").strip()
    if not content:
        return None, "Model did not return skill content."

    skill = Skill(
        title=title,
        description=description,
        content=content,
        created_by=created_by,
        tags=_as_str_list(data.get("tags")),
        triggers=_as_str_list(data.get("triggers")),
        project_types=_as_str_list(data.get("project_types")),
        source="generate",
    )
    apply_inferred_metadata(skill, source="generate")
    return skill, None
