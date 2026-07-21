"""Plan interview workflow — clarify → draft → approve.

Extends permission-mode `/plan` with staged guidance. Product edits stay blocked
until `/plan-exit` / `/plan-approve`.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Optional

from .plan_mode import format_plan_mode_reminder, new_plan_path, plans_dir


class PlanInterviewStage(str, Enum):
    CLARIFY = "clarify"
    DRAFT = "draft"
    READY = "ready"


def plan_interview_enabled() -> bool:
    raw = os.environ.get("Z_PLAN_INTERVIEW", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def latest_plan_path() -> Optional[Path]:
    plans = sorted(plans_dir().glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return plans[0] if plans else None


def detect_stage(
    *,
    active_path: Optional[str] = None,
    forced_stage: Optional[PlanInterviewStage] = None,
) -> PlanInterviewStage:
    if forced_stage is not None:
        return forced_stage
    path = Path(active_path) if active_path else latest_plan_path()
    if path and path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        if len(text) >= 40:
            return PlanInterviewStage.READY
        return PlanInterviewStage.DRAFT
    return PlanInterviewStage.CLARIFY


def format_interview_reminder(
    stage: PlanInterviewStage,
    *,
    plan_path: Optional[Path] = None,
) -> str:
    dest = str(plan_path) if plan_path else str(plans_dir() / "<task>.md")
    base = format_plan_mode_reminder(plan_path)
    if not plan_interview_enabled():
        return base

    if stage is PlanInterviewStage.CLARIFY:
        extra = (
            "# Plan interview — stage: clarify\n"
            "- Ask 1–3 clarifying questions about scope, constraints, and success criteria.\n"
            "- Do NOT write the plan file yet; wait for answers.\n"
            "- After answers, move on: write the plan artifact "
            f"at `{dest}` (or user runs `/plan-draft`).\n"
        )
    elif stage is PlanInterviewStage.DRAFT:
        extra = (
            "# Plan interview — stage: draft\n"
            f"- Write/update the plan markdown artifact at: `{dest}`\n"
            "- Include approach, steps, out-of-scope, and risks.\n"
            "- When the draft is solid, tell the user to `/plan-exit` (or `/plan-approve`).\n"
        )
    else:
        extra = (
            "# Plan interview — stage: ready\n"
            f"- Plan artifact present at `{dest}`.\n"
            "- User should `/plan-exit` / `/plan-approve` to bind it and implement.\n"
            "- You may refine the plan file; still no product edits.\n"
        )
    return base + "\n" + extra


def format_status(stage: PlanInterviewStage, *, plan_path: Optional[str] = None) -> str:
    path = plan_path or (str(latest_plan_path()) if latest_plan_path() else "(none)")
    return (
        f"Plan interview stage: {stage.value}\n"
        f"Plan artifact: {path}\n"
        "Flow: clarify → draft → ready → /plan-exit\n"
    )


def advance_after_user_reply(stage: PlanInterviewStage) -> PlanInterviewStage:
    """After the user answers clarify questions, advance to draft."""
    if stage is PlanInterviewStage.CLARIFY:
        return PlanInterviewStage.DRAFT
    return stage
