"""P1 terminal UX — prompt mode chrome (PLAN› / ASK› / ›)."""

from __future__ import annotations

from typing import Any, Optional

from aider.z.ux_flags import env_truthy


def prompt_chevron() -> str:
    """Return the prompt chevron character (› unless ASCII escape)."""
    if env_truthy("Z_PROMPT_ASCII"):
        return ">"
    return "›"


def resolve_prompt_chrome(
    *,
    forced_task_mode: Any = None,
    edit_format: Optional[str] = None,
    default_edit_format: Optional[str] = None,
    multiline: bool = False,
) -> str:
    """Build the input prompt prefix including trailing space.

    Priority: sticky PLAN → ask/context → help → other non-default format → default.
    """
    from aider.z.task_mode import TaskMode

    chev = prompt_chevron()
    label = ""

    if forced_task_mode is TaskMode.PLAN or (
        isinstance(forced_task_mode, TaskMode) and forced_task_mode is TaskMode.PLAN
    ):
        label = "PLAN"
    elif edit_format in ("ask", "context"):
        label = "ASK"
    elif edit_format == "help":
        label = "help"
    elif edit_format and edit_format != default_edit_format:
        label = str(edit_format)

    if not label and env_truthy("Z_PROMPT_BRAND"):
        label = "Z"

    parts: list[str] = []
    if label:
        parts.append(label)
    if multiline:
        parts.append("multi")

    if parts:
        return " ".join(parts) + chev + " "
    return chev + " "


def format_mode_status_line(
    *,
    mode: str,
    plan_stage: Optional[str] = None,
) -> str:
    """One-line Mode: status after /plan, /ask, /code, etc."""
    mode_u = (mode or "CODE").strip().upper()
    if mode_u == "PLAN":
        stage = (plan_stage or "").strip().lower()
        if stage:
            return f"Mode: PLAN ({stage}) — product edits blocked until /plan-exit"
        return "Mode: PLAN — product edits blocked until /plan-exit"
    if mode_u in ("ASK", "CONTEXT"):
        return "Mode: ASK — questions only; no product edits"
    if mode_u == "HELP":
        return "Mode: HELP — usage and troubleshooting"
    return "Mode: CODE — product edits allowed"
