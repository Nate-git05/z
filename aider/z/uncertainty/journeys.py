"""Critical user journey planning — acceptance scenarios before implementation.

End-to-end tests are derived from acceptance criteria *before* coding.
Passing isolated unit tests cannot substitute for critical journey evidence.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence


# Evidence types that can resolve a journey / uncertainty node
EVIDENCE_MULTI_SESSION_E2E = "multi_session_e2e"
EVIDENCE_BROWSER_E2E = "browser_e2e"
EVIDENCE_INTEGRATION = "integration_test"
EVIDENCE_UNIT = "unit_test"
EVIDENCE_PRODUCTION_BUILD = "production_build"
EVIDENCE_FILE_INSPECTION = "file_inspection"
EVIDENCE_EXECUTION = "execution"


@dataclass
class JourneyStep:
    """One observable step in a critical user journey."""

    index: int
    action: str
    observation: str
    evidence_type: str = EVIDENCE_BROWSER_E2E

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CriticalJourney:
    """One mandatory end-to-end acceptance scenario."""

    id: str
    title: str
    steps: List[JourneyStep] = field(default_factory=list)
    required_evidence_type: str = EVIDENCE_BROWSER_E2E
    # Status of evidence binding
    status: str = "planned"  # planned | in_progress | verified | unverified
    evidence_notes: List[str] = field(default_factory=list)
    risk: str = "critical"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "steps": [s.to_dict() for s in self.steps],
            "required_evidence_type": self.required_evidence_type,
            "status": self.status,
            "evidence_notes": list(self.evidence_notes),
            "risk": self.risk,
        }

    @property
    def has_passing_evidence(self) -> bool:
        return self.status == "verified"


@dataclass
class JourneyPlan:
    """All critical journeys for a task."""

    journeys: List[CriticalJourney] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"journeys": [j.to_dict() for j in self.journeys]}

    @property
    def all_verified(self) -> bool:
        crit = [j for j in self.journeys if j.risk == "critical"]
        if not crit:
            return True
        return all(j.has_passing_evidence for j in crit)

    @property
    def unverified_critical(self) -> List[CriticalJourney]:
        return [
            j
            for j in self.journeys
            if j.risk == "critical" and not j.has_passing_evidence
        ]


_MULTIPLAYER_RE = re.compile(
    r"(?i)\b(multiplayer|two\s+players?|player\s*[ab]|lobby|challenge|"
    r"rock[\s-]?paper|best[\s-]of|qr\s*code|guest\s+join)\b"
)
_WEB_FEATURE_RE = re.compile(
    r"(?i)\b(web|browser|page|ui|frontend|next\.?js|react|app)\b"
)
_API_RE = re.compile(r"(?i)\b(api|endpoint|rest|graphql)\b")
_AUTH_RE = re.compile(r"(?i)\b(login|signup|auth|oauth|sign[\s-]?in)\b")


def _steps(pairs: Sequence[tuple[str, str]], evidence: str) -> List[JourneyStep]:
    return [
        JourneyStep(index=i + 1, action=a, observation=o, evidence_type=evidence)
        for i, (a, o) in enumerate(pairs)
    ]


def infer_critical_journeys(requirements: str) -> JourneyPlan:
    """Derive mandatory journeys from the request before implementation."""
    text = requirements or ""
    journeys: List[CriticalJourney] = []

    if _MULTIPLAYER_RE.search(text):
        journeys.append(
            CriticalJourney(
                id="multiplayer_match",
                title="Two-player critical path",
                required_evidence_type=EVIDENCE_MULTI_SESSION_E2E,
                steps=_steps(
                    [
                        ("Start application from a clean checkout", "App serves host page"),
                        ("Open host page", "QR / join destination visible"),
                        ("Open Player A in browser context A", "Player A session independent"),
                        ("Open Player B in browser context B", "Player B session independent"),
                        ("Join the same lobby", "Both players listed for each other"),
                        ("Player A sends challenge", "Player B receives pending challenge"),
                        ("Player B accepts", "Both enter the same match id"),
                        ("Submit hidden choices independently", "Opponent choice not visible early"),
                        ("Resolve a tie / complete best-of series", "Both see same winner and score"),
                        ("Return both players to the lobby", "Lobby state consistent"),
                    ],
                    EVIDENCE_MULTI_SESSION_E2E,
                ),
            )
        )

    if _AUTH_RE.search(text):
        journeys.append(
            CriticalJourney(
                id="auth_sign_in",
                title="Sign-in critical path",
                required_evidence_type=EVIDENCE_BROWSER_E2E,
                steps=_steps(
                    [
                        ("Open login entry", "Auth options visible"),
                        ("Complete sign-in", "Authenticated session established"),
                        ("Refresh / reconnect", "Session still valid or clear re-auth"),
                        ("Access protected action", "Authorized; unauthenticated rejected"),
                    ],
                    EVIDENCE_BROWSER_E2E,
                ),
            )
        )

    if _WEB_FEATURE_RE.search(text) and not journeys:
        journeys.append(
            CriticalJourney(
                id="primary_web_path",
                title="Primary web user journey",
                required_evidence_type=EVIDENCE_BROWSER_E2E,
                steps=_steps(
                    [
                        ("Start app from clean install", "Production or dev server responds"),
                        ("Open primary page", "Entry state understandable"),
                        ("Complete the main user action", "Observable success state"),
                        ("Exercise error / empty path", "User can recover"),
                    ],
                    EVIDENCE_BROWSER_E2E,
                ),
            )
        )

    if _API_RE.search(text) and not any(j.id.startswith("api") for j in journeys):
        journeys.append(
            CriticalJourney(
                id="api_contract_path",
                title="API contract journey",
                required_evidence_type=EVIDENCE_INTEGRATION,
                risk="critical",
                steps=_steps(
                    [
                        ("Start server with real state layer", "Health/ready ok"),
                        ("Call primary endpoint with valid input", "Typed success response"),
                        ("Call with invalid / unauthorized input", "Explicit error, no leak"),
                        ("Repeat idempotent request", "Safe duplicate behavior"),
                    ],
                    EVIDENCE_INTEGRATION,
                ),
            )
        )

    return JourneyPlan(journeys=journeys)


def mark_journey_evidence(
    plan: JourneyPlan,
    journey_id: str,
    *,
    evidence_type: str,
    notes: str = "",
    passed: bool = False,
) -> Optional[CriticalJourney]:
    """
    Bind evidence to a journey. Wrong evidence type cannot verify.

    A unit test of respondChallenge() must not resolve a multi_session_e2e node.
    """
    for j in plan.journeys:
        if j.id != journey_id:
            continue
        if notes:
            j.evidence_notes.append(notes)
        if evidence_type != j.required_evidence_type:
            j.status = "unverified"
            j.evidence_notes.append(
                f"Rejected evidence type '{evidence_type}' "
                f"(required '{j.required_evidence_type}')"
            )
            return j
        j.status = "verified" if passed else "unverified"
        return j
    return None


def format_journey_plan(plan: JourneyPlan) -> str:
    if not plan.journeys:
        return "Critical user journeys: (none inferred)"
    lines = [
        "Critical user journeys (mandatory — unit tests cannot substitute):",
        "",
    ]
    for j in plan.journeys:
        lines.append(
            f"  [{j.status}] {j.title} "
            f"(evidence: {j.required_evidence_type}, risk: {j.risk})"
        )
        for s in j.steps:
            lines.append(f"    {s.index}. {s.action}")
            lines.append(f"       → observe: {s.observation}")
        lines.append("")
    lines.append(
        "Completion rule: critical_user_journeys.every(j => j.has_passing_evidence)."
    )
    lines.append(
        "If browser tools are unavailable, report partial completion — never claim "
        "the central journey works without the required evidence type."
    )
    return "\n".join(lines)


def journey_uncertainty_claims(plan: JourneyPlan) -> List[dict]:
    """Evidence-bound uncertainty nodes for each critical journey."""
    claims = []
    for j in plan.journeys:
        if j.risk != "critical":
            continue
        claims.append(
            {
                "claim": j.title,
                "risk": j.risk,
                "requiredEvidence": {
                    "type": j.required_evidence_type,
                    "observations": [s.observation for s in j.steps[:6]],
                },
                "status": j.status,
                "journey_id": j.id,
            }
        )
    return claims
