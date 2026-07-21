"""Completion gate — minimize false completion rate.

A 9/10 unsupervised agent may stop and say it cannot verify something.
It must almost never say a feature is ready when its central journey is broken.

Primary metric: false completion rate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence

from .architecture import ArchitectureCheckpoint
from .capabilities import CapabilityPlan
from .integrity import IntegrityReport
from .journeys import JourneyPlan
from .schema import (
    Area,
    NodeStatus,
    NodeType,
    TaskChecklist,
    Tier,
    UncertaintyNode,
)
from .verify import VerificationRecord


@dataclass
class CompletionItem:
    """One checkbox in the unsupervised completion gate."""

    id: str
    label: str
    satisfied: bool
    critical: bool = True
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CompletionReport:
    """Full completion assessment for a task."""

    items: List[CompletionItem] = field(default_factory=list)
    complete: bool = False
    partial: bool = False
    blocked_reasons: List[str] = field(default_factory=list)
    user_message: str = ""

    def to_dict(self) -> dict:
        return {
            "items": [i.to_dict() for i in self.items],
            "complete": self.complete,
            "partial": self.partial,
            "blocked_reasons": list(self.blocked_reasons),
            "user_message": self.user_message,
        }


def evaluate_completion(
    *,
    verification: Optional[VerificationRecord] = None,
    checklist: Optional[TaskChecklist] = None,
    journeys: Optional[JourneyPlan] = None,
    integrity: Optional[IntegrityReport] = None,
    architecture: Optional[ArchitectureCheckpoint] = None,
    capabilities: Optional[CapabilityPlan] = None,
    verification_weakened: bool = False,
    clean_install_ok: Optional[bool] = None,
    production_build_ok: Optional[bool] = None,
    production_start_ok: Optional[bool] = None,
    smoke_ok: Optional[bool] = None,
    unresolved_critical_nodes: int = 0,
    unintended_artifacts: Sequence[str] = (),
    ux_applicable: bool = False,
    ux_verification_pending: int = 0,
    multi_session_required: bool = False,
    multi_session_verified: bool = False,
    evidence_stale: bool = False,
) -> CompletionReport:
    """
    Only declare completion when all critical items are satisfied.

    Missing evidence ⇒ partial completion, never success.
    """
    items: List[CompletionItem] = []
    reasons: List[str] = []

    def add(cid: str, label: str, ok: bool, *, critical: bool = True, detail: str = ""):
        items.append(
            CompletionItem(
                id=cid, label=label, satisfied=ok, critical=critical, detail=detail
            )
        )
        if critical and not ok:
            reasons.append(detail or label)

    # Product requirements with correctly typed evidence
    req_ok = True
    req_detail = "No checklist"
    if checklist and checklist.items:
        productish = [
            i
            for i in checklist.items
            if (getattr(i, "kind", "product") or "product")
            in ("product", "quality", "verification")
        ]
        unfinished = [
            i
            for i in productish
            if (i.status or "") not in ("Fully Addressed", "Unverifiable")
        ]
        req_ok = not unfinished
        if unfinished:
            req_detail = (
                f"{len(unfinished)} requirement(s) lack typed evidence: "
                + "; ".join(i.text[:60] for i in unfinished[:3])
            )
        else:
            req_detail = "All product/quality/verification items addressed or unverifiable"
    add(
        "requirements_evidence",
        "Every product requirement has correctly typed evidence",
        req_ok,
        detail=req_detail,
    )

    # Critical user journeys
    journey_ok = True
    journey_detail = "No critical journeys inferred"
    if journeys and journeys.journeys:
        unverified = journeys.unverified_critical
        journey_ok = not unverified
        if unverified:
            titles = ", ".join(j.title for j in unverified[:3])
            journey_detail = (
                f"Critical journey(s) lack required evidence type: {titles}. "
                "Lower-level checks cannot substitute."
            )
        else:
            journey_detail = "All critical journeys have passing evidence"
    add(
        "critical_journeys",
        "Every critical user journey passes end-to-end",
        journey_ok,
        detail=journey_detail,
    )

    # Verification suite
    meaningful = bool(verification and verification.meaningful_pass)
    add(
        "verification_pass",
        "Real typecheck / lint / unit / integration checks pass",
        meaningful,
        detail=(
            "Verification meaningfully passed"
            if meaningful
            else (
                (verification.error or verification.output_excerpt or "verification did not pass")
                if verification
                else "No verification record"
            )[:300]
        ),
    )

    # Integrity
    integrity_ok = not verification_weakened and not (
        integrity and integrity.blocked
    )
    add(
        "verification_integrity",
        "No verification mechanism was weakened",
        integrity_ok,
        detail=(
            "Verification strength preserved"
            if integrity_ok
            else "Verification weakening detected — blocked"
        ),
    )

    # Architecture
    arch_ok = True
    arch_detail = "No architecture checkpoint"
    if architecture and architecture.items:
        arch_ok = architecture.all_critical_known or not architecture.blocking_assumptions
        # If there are blocking assumptions still listed as unknown, fail
        unknown_critical = [
            i for i in architecture.items if i.critical and i.status == "unknown"
        ]
        arch_ok = not unknown_critical
        arch_detail = (
            "Architecture checkpoint satisfied"
            if arch_ok
            else f"{len(unknown_critical)} critical architecture item(s) unknown"
        )
    add(
        "architecture",
        "Architecture risks reviewed (or not applicable)",
        arch_ok,
        critical=bool(architecture and architecture.items),
        detail=arch_detail,
    )

    # Capability gaps acknowledged (always ok if compensated — we check presence)
    if capabilities and capabilities.coverage_gaps:
        add(
            "capability_compensation",
            "Capability gaps have explicit compensation workflows",
            bool(capabilities.compensation),
            critical=any(g.critical for g in capabilities.coverage_gaps),
            detail=(
                f"{len(capabilities.coverage_gaps)} gap(s); "
                f"{len(capabilities.compensation)} compensation note(s)"
            ),
        )

    # Clean-room / production (optional — unknown means unverified, not fail
    # unless the task inferred production_build capability)
    needs_prod = bool(
        capabilities
        and any(c.id == "production_build" for c in capabilities.required)
    )
    if clean_install_ok is not None or needs_prod:
        add(
            "clean_install",
            "Clean dependency installation succeeds",
            clean_install_ok is True,
            critical=needs_prod,
            detail=(
                "Clean install ok"
                if clean_install_ok is True
                else "Clean install not verified"
            ),
        )
    if production_build_ok is not None or needs_prod:
        add(
            "production_build",
            "Production build passes",
            production_build_ok is True,
            critical=needs_prod,
            detail=(
                "Production build ok"
                if production_build_ok is True
                else "Production build not verified"
            ),
        )
    if production_start_ok is not None or needs_prod:
        add(
            "production_start",
            "Built application starts",
            production_start_ok is True,
            critical=needs_prod,
            detail=(
                "Start ok"
                if production_start_ok is True
                else "Built app start not verified"
            ),
        )
    if smoke_ok is not None or needs_prod:
        add(
            "smoke",
            "Smoke test passes against the built application",
            smoke_ok is True,
            critical=needs_prod,
            detail="Smoke ok" if smoke_ok is True else "Smoke not verified",
        )

    add(
        "unresolved_critical",
        "No unresolved critical uncertainty remains",
        unresolved_critical_nodes == 0,
        detail=(
            "No critical nodes open"
            if unresolved_critical_nodes == 0
            else f"{unresolved_critical_nodes} critical uncertainty node(s) open"
        ),
    )

    artifacts = [a for a in unintended_artifacts if a]
    add(
        "artifact_hygiene",
        "Working tree contains no unintended agent artifacts",
        not artifacts,
        critical=bool(artifacts),
        detail="Clean" if not artifacts else f"Artifacts: {', '.join(artifacts[:5])}",
    )

    if ux_applicable:
        add(
            "ux_verification",
            "UX viewport / a11y / state checklist addressed",
            ux_verification_pending == 0,
            critical=False,
            detail=(
                "UX checks done"
                if ux_verification_pending == 0
                else f"{ux_verification_pending} UX check(s) still pending"
            ),
        )

    if multi_session_required:
        add(
            "multi_session",
            "Multi-session browser journey verified",
            multi_session_verified,
            critical=True,
            detail=(
                "Multi-session evidence present"
                if multi_session_verified
                else "Two-user journey unverified — PARTIAL only"
            ),
        )

    add(
        "evidence_fresh",
        "Evidence is current for the final code state",
        not evidence_stale,
        critical=evidence_stale,
        detail="Evidence fresh" if not evidence_stale else "Evidence stale after later edits",
    )

    critical_items = [i for i in items if i.critical]
    all_critical_ok = all(i.satisfied for i in critical_items)
    any_ok = any(i.satisfied for i in items)

    if all_critical_ok:
        msg = (
            "Completion gate: all critical checks satisfied. "
            "Evidence is current for the final code state."
        )
        complete, partial = True, False
    else:
        # Honest partial completion message (Codex recommendation)
        unverified_journeys = []
        if journeys:
            unverified_journeys = [j.title for j in journeys.unverified_critical]
        if unverified_journeys:
            msg = (
                "PARTIAL COMPLETION — do not claim the feature is ready.\n"
                "The implementation and lower-level checks may be complete, but "
                f"the central journey remains unverified: "
                f"{', '.join(unverified_journeys)}.\n"
                "Passing unit tests cannot substitute for the required evidence type."
            )
        else:
            msg = (
                "PARTIAL COMPLETION — do not claim the feature is ready.\n"
                "Critical completion items unverified:\n"
                + "\n".join(f"  - {r}" for r in reasons[:8])
            )
        complete, partial = False, any_ok or bool(items)

    return CompletionReport(
        items=items,
        complete=complete,
        partial=partial,
        blocked_reasons=reasons,
        user_message=msg,
    )


def completion_nodes_from_report(
    report: CompletionReport,
    *,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
) -> List[UncertaintyNode]:
    """Raise a High node when false completion would otherwise be claimed."""
    if report.complete:
        return []
    critical_fail = [i for i in report.items if i.critical and not i.satisfied]
    if not critical_fail:
        return []
    return [
        UncertaintyNode(
            title="False-completion risk — critical evidence missing",
            type=NodeType.FALSE_COMPLETION_RISK,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.HIGH,
            summary=report.user_message.split("\n")[0][:200],
            explanation=report.user_message,
            files_affected=[],
            why_uncertain=(
                "Apparent success from lower-level checks is misleading without "
                "correctly typed evidence for critical journeys/requirements."
            ),
            what_could_go_wrong=(
                "Shipping a feature whose central user journey is broken while "
                "unit tests and typecheck are green."
            ),
            suggested_fix=(
                "Gather the required evidence type for each failing completion "
                "item, or explicitly report partial completion to the human."
            ),
            suggested_prompt=report.user_message,
            status=NodeStatus.OPEN,
            area=Area.TESTS,
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            signals={
                "false_completion_risk": True,
                "verification_blocked": True,
                "completion_failures": [i.id for i in critical_fail],
            },
        )
    ]


def format_completion_report(report: CompletionReport) -> str:
    lines = ["Completion gate:", ""]
    for i in report.items:
        mark = "[x]" if i.satisfied else "[ ]"
        crit = " (critical)" if i.critical else ""
        lines.append(f"  {mark} {i.label}{crit}")
        if i.detail and not i.satisfied:
            lines.append(f"      → {i.detail}")
    lines.append("")
    if report.complete:
        lines.append("Status: COMPLETE")
    else:
        lines.append("Status: PARTIAL — do not claim readiness")
    lines.append(report.user_message)
    return "\n".join(lines)
