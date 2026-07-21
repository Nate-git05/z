"""Verify-before-done soft stop — block false completion claims mid-turn."""

from __future__ import annotations

import os
import re
from typing import Optional, Sequence

_DONE_CLAIM_RE = re.compile(
    r"(?i)\b("
    r"all done|that's done|that is done|task is complete|we're done|we are done|"
    r"fixed(?: it| the bug)?|this is (?:now )?fixed|ready to (?:commit|ship|merge)|"
    r"implementation is complete|fully (?:implemented|fixed)|"
    r"no further changes needed|nothing else to do|"
    r"should be good(?: to go)?|you're all set|you are all set"
    r")\b"
)


def done_soft_stop_enabled() -> bool:
    raw = os.environ.get("Z_DONE_SOFT_STOP", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def looks_like_done_claim(text: str) -> bool:
    if not text or len(text.strip()) < 8:
        return False
    return bool(_DONE_CLAIM_RE.search(text))


def soft_stop_reason(
    *,
    open_high_count: int = 0,
    last_verify_failed: bool = False,
    plan_pending: bool = False,
    completion_incomplete: bool = False,
) -> Optional[str]:
    """
    Return a reflect message if claiming done is premature; else None.
    """
    if not done_soft_stop_enabled():
        return None
    reasons = []
    if plan_pending:
        reasons.append("an implementation plan is still required/unapproved")
    if open_high_count > 0:
        reasons.append(
            f"{open_high_count} High uncertainty node(s) are still open "
            "(`/uncertainties`)"
        )
    if last_verify_failed:
        reasons.append("the last verification/test run failed")
    if completion_incomplete:
        reasons.append("the completion gate reports PARTIAL (critical evidence missing)")
    if not reasons:
        return None
    joined = "; ".join(reasons)
    return (
        "Soft stop — do not claim the task complete yet: "
        f"{joined}. "
        "Resolve or explicitly acknowledge these, then continue."
    )


def count_open_high(nodes: Sequence) -> int:
    from aider.z.uncertainty.schema import NodeStatus, Tier

    n = 0
    for node in nodes or []:
        status = getattr(node, "status", None)
        if status in (NodeStatus.RESOLVED, NodeStatus.IGNORED):
            continue
        # Prefer effective risk; fall back to risk_tier
        tier = getattr(node, "risk_tier", None)
        if tier == Tier.HIGH:
            n += 1
    return n
