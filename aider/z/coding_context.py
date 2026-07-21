"""Coding-context hygiene — compact directives for the coder turn.

Inspired by OpenCode/Claude Code: keep the *coding* prompt thin. Skills and
capability guidance become short directives; full playbooks stay on disk.
Uncertainty/verify remain the control plane and are unchanged here.
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence

# Soft body budget per skill when compact-injecting (chars).
DEFAULT_SKILL_BODY_CHARS = 1200


def skill_inject_full_enabled() -> bool:
    """Escape hatch: restore legacy full-body skill injection."""
    return os.environ.get("Z_SKILL_INJECT_FULL", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def strict_chat_edits_enabled() -> bool:
    """
    Require existing files to already be in the chat before edits apply.

    Default ON. Set Z_STRICT_CHAT_EDITS=0 for legacy confirm-ask behavior
    (which --yes-always can auto-approve).
    """
    raw = os.environ.get("Z_STRICT_CHAT_EDITS", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def skill_body_budget() -> int:
    raw = os.environ.get("Z_SKILL_BODY_CHARS", "").strip()
    if raw.isdigit():
        return max(200, int(raw))
    return DEFAULT_SKILL_BODY_CHARS


def _truncate_body(text: str, budget: int) -> tuple[str, bool]:
    text = (text or "").strip()
    if len(text) <= budget:
        return text, False
    # Prefer breaking on a newline near the budget
    cut = text[:budget]
    nl = cut.rfind("\n")
    if nl > budget // 2:
        cut = cut[:nl]
    return cut.rstrip() + "\n… [skill body truncated — open full skill on disk]\n", True


def format_skill_directive(skill, *, body_budget: Optional[int] = None) -> str:
    """
    Compact directive for one skill — enough to steer, not a full dump.
    """
    budget = body_budget if body_budget is not None else skill_body_budget()
    kind = getattr(skill, "kind", None) or "playbook"
    title = getattr(skill, "title", None) or "untitled"
    desc = (getattr(skill, "description", None) or "").strip()
    langs = list(getattr(skill, "languages", None) or [])
    capability = (getattr(skill, "capability", None) or "").strip()
    path = (getattr(skill, "path", None) or "").strip()
    content = getattr(skill, "content", None) or ""

    lines: List[str] = [f"### Skill directive: {title} [{kind}]"]
    if desc:
        lines.append(f"_{desc}_")
    meta_bits = []
    if langs:
        meta_bits.append(f"languages={','.join(langs)}")
    if capability:
        meta_bits.append(f"capability={capability}")
    if path:
        meta_bits.append(f"full_skill_path={path}")
    if meta_bits:
        lines.append(" · ".join(meta_bits))
    body, truncated = _truncate_body(content, budget)
    if body:
        lines.append("")
        lines.append(body)
    if truncated and path:
        lines.append(f"(Full playbook: `{path}`)")
    return "\n".join(lines).rstrip()


def format_skills_compact(
    skills: Sequence,
    *,
    checkpoint: str = "turn",
    body_budget: Optional[int] = None,
) -> str:
    """
    Compact multi-skill block for cur_messages.

    Bug-pattern skills keep the hypothesis formatter (already compact).
    Playbooks use directives instead of full markdown bodies.
    """
    if not skills:
        return ""

    # Lazy import to avoid cycles; bug-pattern formatter lives in session.
    from aider.z.skills.schema import SKILL_KIND_BUG_PATTERN
    from aider.z.skills.session import format_bug_pattern_hypothesis

    patterns = [s for s in skills if (getattr(s, "kind", None) or "") == SKILL_KIND_BUG_PATTERN]
    others = [s for s in skills if (getattr(s, "kind", None) or "") != SKILL_KIND_BUG_PATTERN]

    parts: List[str] = []
    if patterns:
        parts.append(
            "Bug-pattern matches for this task (hypotheses — do not auto-apply):"
        )
        parts.append("")
        for s in patterns:
            parts.append(format_bug_pattern_hypothesis(s))
            parts.append("")

    if others:
        if checkpoint == "reflect":
            header = (
                "Compact skill directives for this workflow step "
                "(follow these; full playbooks remain on disk):"
            )
        else:
            header = (
                "Compact skill directives for this task "
                "(follow these; full playbooks remain on disk):"
            )
        parts.append(header)
        parts.append("")
        for s in others:
            parts.append(format_skill_directive(s, body_budget=body_budget))
            parts.append("")

    return "\n".join(parts).strip()


def coding_quality_reminder() -> str:
    """
    Short implement-mode reminder (OpenCode-style discipline, Z-shaped).

    Injected into final_reminders — not a second system essay.
    """
    return (
        "# Coding quality (Z)\n"
        "- Inspect files already in the chat before proposing SEARCH/REPLACE on them.\n"
        "- Existing files must be added to the chat before edit; create new files only when needed.\n"
        "- Match existing conventions and dependencies; do not invent packages.\n"
        "- After non-trivial edits, run the project's known tests/lint/typecheck when available.\n"
        "- Keep replies concise; do not paste uncertainty trees or full skill bodies into edits.\n"
        "- For read-only lookups mid-turn, you may emit a ```z-tool fence with read/grep/glob/ls "
        "(bounded); prefer `/add` before editing.\n"
    )
