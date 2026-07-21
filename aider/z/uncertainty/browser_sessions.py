"""Multi-session browser testing hooks.

For collaborative / multiplayer features, automatically plan (and when tools
exist, run) separate browser contexts. When browser tools are unavailable,
return an honest unverified result so completion stays PARTIAL.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence

from .journeys import (
    EVIDENCE_MULTI_SESSION_E2E,
    CriticalJourney,
    JourneyPlan,
    mark_journey_evidence,
)


@dataclass
class BrowserContextPlan:
    """One independent browser context (player/session)."""

    id: str
    label: str
    role: str = "player"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MultiSessionPlan:
    contexts: List[BrowserContextPlan] = field(default_factory=list)
    base_url: str = ""
    steps: List[str] = field(default_factory=list)
    required: bool = False

    def to_dict(self) -> dict:
        return {
            "contexts": [c.to_dict() for c in self.contexts],
            "base_url": self.base_url,
            "steps": list(self.steps),
            "required": self.required,
        }


@dataclass
class MultiSessionResult:
    plan: MultiSessionPlan
    tools_available: bool = False
    tool_name: str = ""
    ran: bool = False
    passed: bool = False
    detail: str = ""
    observations: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "plan": self.plan.to_dict(),
            "tools_available": self.tools_available,
            "tool_name": self.tool_name,
            "ran": self.ran,
            "passed": self.passed,
            "detail": self.detail,
            "observations": list(self.observations),
        }


def detect_browser_tools() -> tuple[bool, str]:
    """
    Detect whether a multi-session browser runner is available.

    Order: env override → playwright CLI → cypress → puppeteer project script.
    """
    override = os.environ.get("Z_BROWSER_TOOL", "").strip()
    if override:
        if override.lower() in ("0", "none", "off", "unavailable"):
            return False, ""
        return True, override
    if shutil.which("playwright"):
        return True, "playwright"
    if shutil.which("npx"):
        # npx present does not guarantee playwright installed — still a candidate
        return True, "npx playwright"
    if shutil.which("cypress"):
        return True, "cypress"
    return False, ""


def draft_multi_session_plan(
    requirements: str,
    *,
    base_url: str = "http://127.0.0.1:3000",
    journey: Optional[CriticalJourney] = None,
) -> MultiSessionPlan:
    text = requirements or ""
    needed = bool(
        __import__("re").search(
            r"(?i)\b(multiplayer|two\s+players?|player\s*[ab]|multi-?session|"
            r"collaborat|lobby|guest\s+join)\b",
            text,
        )
    )
    if not needed:
        return MultiSessionPlan(required=False)

    contexts = [
        BrowserContextPlan(id="ctx_a", label="Player A", role="host"),
        BrowserContextPlan(id="ctx_b", label="Player B", role="guest"),
    ]
    if journey and journey.steps:
        steps = [f"{s.index}. {s.action} → {s.observation}" for s in journey.steps]
    else:
        steps = [
            "1. Open host page in context A",
            "2. Open join URL in context B (independent storage/cookies)",
            "3. Confirm each sees the other in the lobby",
            "4. Drive challenge → accept → hidden choices → result",
            "5. Confirm synchronized winner/score; return to lobby",
        ]
    return MultiSessionPlan(
        contexts=contexts,
        base_url=base_url,
        steps=steps,
        required=True,
    )


def run_multi_session(
    plan: MultiSessionPlan,
    *,
    journeys: Optional[JourneyPlan] = None,
) -> MultiSessionResult:
    """
    Attempt multi-session verification.

    Without a real browser harness this returns ``ran=False`` with an honest
    detail string — completion must stay PARTIAL.
    """
    result = MultiSessionResult(plan=plan)
    if not plan.required:
        result.detail = "multi-session not required for this task"
        return result

    available, tool = detect_browser_tools()
    result.tools_available = available
    result.tool_name = tool

    if not available:
        result.ran = False
        result.passed = False
        result.detail = (
            "Browser tools unavailable. The implementation and lower-level "
            "checks may be complete, but the central two-user journey remains "
            "unverified. Do not claim the multiplayer feature is ready."
        )
        if journeys:
            for j in journeys.journeys:
                if j.required_evidence_type == EVIDENCE_MULTI_SESSION_E2E:
                    mark_journey_evidence(
                        journeys,
                        j.id,
                        evidence_type="unit_test",
                        notes="rejected: browser tools unavailable",
                        passed=False,
                    )
        return result

    # Tools exist but we do not auto-drive a full UI without a project script.
    # Record readiness and require an explicit project E2E command.
    e2e_cmd = os.environ.get("Z_MULTI_SESSION_E2E_CMD", "").strip()
    if not e2e_cmd:
        result.ran = False
        result.passed = False
        result.detail = (
            f"Browser tool detected ({tool}) but Z_MULTI_SESSION_E2E_CMD is unset. "
            "Set it to the project's multi-context E2E command, or run the "
            "journey manually and bind multi_session_e2e evidence. "
            "Until then: PARTIAL COMPLETION only."
        )
        return result

    from aider.run_cmd import run_cmd

    code, out = run_cmd(e2e_cmd, verbose=False)
    result.ran = True
    result.passed = code == 0
    result.detail = (out or "")[-1500:]
    if result.passed and journeys:
        for j in journeys.journeys:
            if j.required_evidence_type == EVIDENCE_MULTI_SESSION_E2E:
                mark_journey_evidence(
                    journeys,
                    j.id,
                    evidence_type=EVIDENCE_MULTI_SESSION_E2E,
                    notes=f"passed via {e2e_cmd}",
                    passed=True,
                )
                result.observations.append(f"verified journey {j.id}")
    return result


def format_multi_session(plan: MultiSessionPlan, result: Optional[MultiSessionResult] = None) -> str:
    if not plan.required:
        return "Multi-session browser plan: (not required)"
    lines = [
        "Multi-session browser plan (mandatory for collaborative features):",
        f"  Base URL: {plan.base_url or '(unset)'}",
        "  Contexts:",
    ]
    for c in plan.contexts:
        lines.append(f"    - {c.id}: {c.label} ({c.role})")
    lines.append("  Steps:")
    for s in plan.steps:
        lines.append(f"    {s}")
    if result:
        lines.append("")
        lines.append(
            f"  Tools: {'available (' + result.tool_name + ')' if result.tools_available else 'UNAVAILABLE'}"
        )
        lines.append(f"  Ran: {result.ran}  Passed: {result.passed}")
        if result.detail:
            lines.append(f"  Detail: {result.detail[:400]}")
    else:
        lines.append("")
        lines.append(
            "  If browser tools are unavailable, report partial completion — "
            "never claim the two-user journey works."
        )
    return "\n".join(lines)
