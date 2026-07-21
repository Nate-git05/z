"""First-class task modes that gate the agent control-flow pipeline.

``edit_format`` remains a response-rendering setting. ``TaskMode`` answers:
should implementation machinery (planning, capability inference, edits) run
for *this* message?
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class TaskMode(Enum):
    ASK = "ask"
    INVESTIGATE = "investigate"
    IMPLEMENT = "implement"
    REVIEW = "review"
    VERIFY = "verify"

    # --- pipeline policy (single source of truth) ---------------------------

    @property
    def allows_planning(self) -> bool:
        return self is TaskMode.IMPLEMENT

    @property
    def allows_requirement_decomposition(self) -> bool:
        # INVESTIGATE may build investigation targets; REVIEW limited clauses;
        # IMPLEMENT full checklist. ASK/VERIFY skip.
        return self in (TaskMode.IMPLEMENT, TaskMode.INVESTIGATE, TaskMode.REVIEW)

    @property
    def allows_capability_inference(self) -> bool:
        return self is TaskMode.IMPLEMENT

    @property
    def allows_edits(self) -> bool:
        return self is TaskMode.IMPLEMENT

    @property
    def skills_read_only(self) -> bool:
        return self is not TaskMode.IMPLEMENT

    @property
    def allows_shell_readonly(self) -> bool:
        return True  # all modes may run read-only shell to answer

    @property
    def allows_shell_verification(self) -> bool:
        return self in (TaskMode.IMPLEMENT, TaskMode.VERIFY, TaskMode.REVIEW)

    @property
    def allows_shell_mutation(self) -> bool:
        return self is TaskMode.IMPLEMENT


_INVESTIGATE_RE = re.compile(
    r"(?i)\b("
    r"investigate|diagnose|determine why|figure out why|explain why|"
    r"why does|why is|what causes|root cause|do not edit|don't edit|"
    r"without (?:editing|changing|modifying)|no edits?|read[- ]only|"
    r"just (?:look|inspect|check|explain)|find (?:which|what|where)"
    r")\b"
)
_REVIEW_RE = re.compile(
    r"(?i)\b(review (?:this|the|my)|look (?:over|at) (?:this|the) (?:diff|pr|change)|"
    r"code review|please review)\b"
)
_VERIFY_RE = re.compile(
    r"(?i)\b(run (?:the )?tests?|verify (?:that|the)|check (?:if|whether)|"
    r"typecheck|lint (?:the|this)|does (?:it|this) pass)\b"
)
_IMPLEMENT_RE = re.compile(
    r"(?i)\b("
    r"implement|build|add|create|fix|change|update|refactor|write|"
    r"make (?:it|a|the)|please (?:add|fix|implement|build)|ship"
    r")\b"
)


def classify_task_mode(
    edit_format: Optional[str],
    user_message: str = "",
    *,
    intent_mode: Optional[str] = None,
) -> TaskMode:
    """
    Resolve TaskMode for one user message.

    Priority:
      1. Explicit command via edit_format (ask/context → ASK)
      2. Intent.mode from structured extraction when provided
      3. Conservative prompt heuristics; default IMPLEMENT
    """
    fmt = (edit_format or "").strip().lower()
    if fmt in ("ask", "context"):
        # Explicit /ask or /context — hard mapping
        text = user_message or ""
        if intent_mode == "investigate" or _INVESTIGATE_RE.search(text):
            return TaskMode.INVESTIGATE
        return TaskMode.ASK

    if intent_mode:
        try:
            return TaskMode(intent_mode)
        except ValueError:
            pass

    text = (user_message or "").strip()
    if not text:
        return TaskMode.IMPLEMENT

    # Explicit read-only / investigate without a competing implement request
    if _INVESTIGATE_RE.search(text) and not (
        _IMPLEMENT_RE.search(text)
        and not re.search(r"(?i)\bdo not\b|\bdon't\b|\bwithout\b", text)
    ):
        # "investigate … do not edit" → INVESTIGATE
        # "why does X fail and can you fix it" has implement signal → IMPLEMENT
        if re.search(r"(?i)\b(fix|implement|change|add|create|update)\b", text) and not re.search(
            r"(?i)\b(do not|don't|without)\s+(?:edit|change|modify|touch|add|create)",
            text,
        ):
            # mixed: question + fix request → IMPLEMENT for the follow-up half
            if re.search(r"(?i)\b(and|then)\s+(?:can you |please )?fix\b", text):
                return TaskMode.IMPLEMENT
        return TaskMode.INVESTIGATE

    if _REVIEW_RE.search(text) and not _IMPLEMENT_RE.search(text):
        return TaskMode.REVIEW

    if _VERIFY_RE.search(text) and not _IMPLEMENT_RE.search(text):
        return TaskMode.VERIFY

    return TaskMode.IMPLEMENT


def mode_from_edit_format(edit_format: Optional[str]) -> Optional[TaskMode]:
    """Hard mapping for explicit commands only; None if not decisive."""
    fmt = (edit_format or "").strip().lower()
    if fmt in ("ask", "context"):
        return TaskMode.ASK
    return None
