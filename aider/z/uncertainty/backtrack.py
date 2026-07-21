"""Causal uncertainty backtracking.

For each failure:
  Preserve the exact failure.
  Classify the failure layer.
  Locate the assumption that predicted success.
  Walk upward through parent assumptions.
  Find the earliest unsupported or contradicted node.
  Repair that node.
  Invalidate all dependent evidence.
  Rerun checks from that point downward.
  Stop if the proposed repair weakens verification.

Symptom fixes (editing the detector) are rejected in favor of repairing the
earliest unsupported parent assumption.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence

from .evidence import EvidenceLedger
from .failure_classify import FailureClassification, classify_failure
from .schema import (
    Area,
    NodeStatus,
    NodeType,
    Tier,
    UncertaintyNode,
)


@dataclass
class AssumptionNode:
    """One assumption in the causal chain (parent → children)."""

    id: str
    claim: str
    status: str = "assumed"  # assumed | supported | contradicted | repaired
    parent_id: Optional[str] = None
    evidence_types: List[str] = field(default_factory=list)
    layer: str = ""  # maps to failure_classify layers when applicable

    def to_dict(self) -> dict:
        return asdict(self)


# Default causal chain for verification success predictions
_DEFAULT_CHAIN: Sequence[tuple[str, str, Optional[str], str]] = (
    ("env_ready", "Environment and toolchain are prepared", None, "command_not_found"),
    (
        "deps_installed",
        "Dependencies install from the lockfile / valid manifests",
        "env_ready",
        "dependency_install",
    ),
    (
        "types_match",
        "Implementation matches declared types/APIs",
        "deps_installed",
        "type_error",
    ),
    (
        "behavior_matches",
        "Behavior matches the tested contract",
        "types_match",
        "assertion",
    ),
    (
        "build_valid",
        "Framework structure and production build are valid",
        "behavior_matches",
        "build_framework",
    ),
    (
        "journey_works",
        "Critical user journey works end-to-end",
        "build_valid",
        "",
    ),
)


@dataclass
class BacktrackResult:
    """Outcome of walking the causal chain for a failure."""

    classification: FailureClassification
    chain: List[AssumptionNode] = field(default_factory=list)
    earliest: Optional[AssumptionNode] = None
    invalidated_evidence_kinds: List[str] = field(default_factory=list)
    repair_guidance: str = ""
    weaken_blocked: bool = False

    def to_dict(self) -> dict:
        return {
            "classification": self.classification.to_dict(),
            "chain": [c.to_dict() for c in self.chain],
            "earliest": self.earliest.to_dict() if self.earliest else None,
            "invalidated_evidence_kinds": list(self.invalidated_evidence_kinds),
            "repair_guidance": self.repair_guidance,
            "weaken_blocked": self.weaken_blocked,
        }


def build_default_assumption_chain() -> List[AssumptionNode]:
    return [
        AssumptionNode(
            id=nid,
            claim=claim,
            parent_id=parent,
            layer=layer,
            evidence_types=["execution"] if layer else ["multi_session_e2e", "browser_e2e"],
        )
        for nid, claim, parent, layer in _DEFAULT_CHAIN
    ]


def backtrack_failure(
    *,
    output: str = "",
    error: str = "",
    command: str = "",
    exit_code: Optional[int] = None,
    failure_kind: str = "",
    ledger: Optional[EvidenceLedger] = None,
    proposed_repair_touches_detector: bool = False,
) -> BacktrackResult:
    """
    Walk from the failure layer up to the earliest unsupported assumption.
    """
    cls = classify_failure(
        output=output,
        error=error,
        command=command,
        exit_code=exit_code,
        failure_kind=failure_kind,
    )
    chain = build_default_assumption_chain()
    by_id: Dict[str, AssumptionNode] = {n.id: n for n in chain}

    # Mark the matching layer as contradicted; parents above as the backtrack path
    target: Optional[AssumptionNode] = None
    for node in chain:
        if node.layer and node.layer == cls.layer:
            node.status = "contradicted"
            target = node
            break
    if target is None:
        # Unknown → contradict the nearest success prediction (behavior)
        target = by_id.get("behavior_matches") or chain[-1]
        target.status = "contradicted"

    # Walk to earliest: the contradicted node itself is the repair target when
    # its parent is still assumed/supported; if parent is also weak, go up.
    earliest = target
    cursor = target
    while cursor and cursor.parent_id:
        parent = by_id.get(cursor.parent_id)
        if not parent:
            break
        # If we have fresh evidence for the parent layer's prerequisite, stop
        parent_ok = False
        if ledger and parent.layer:
            # map rough kinds
            kind_map = {
                "command_not_found": "clean_install",
                "dependency_install": "clean_install",
                "type_error": "typecheck",
                "assertion": "unit",
                "build_framework": "build",
            }
            kind = kind_map.get(parent.layer)
            if kind and ledger.fresh_pass(kind):
                parent.status = "supported"
                parent_ok = True
        if parent_ok:
            break
        # Parent not evidenced → it becomes the earlier unsupported node
        if parent.status == "assumed":
            earliest = parent
        cursor = parent

    if earliest:
        earliest.status = "contradicted"

    invalidated: List[str] = []
    if ledger:
        # Invalidate everything at or below the earliest node
        order = [n.id for n in chain]
        if earliest and earliest.id in order:
            start = order.index(earliest.id)
            for nid in order[start:]:
                invalidated.append(nid)
        # Also mark ledger records stale for dependent kinds
        kind_for = {
            "env_ready": "clean_install",
            "deps_installed": "clean_install",
            "types_match": "typecheck",
            "behavior_matches": "unit",
            "build_valid": "build",
            "journey_works": "smoke",
        }
        for nid in invalidated:
            k = kind_for.get(nid)
            if not k:
                continue
            for rec in ledger.records:
                if rec.kind == k and not rec.stale:
                    rec.stale = True

    weaken_blocked = bool(proposed_repair_touches_detector)
    guidance = (
        f"Earliest unsupported assumption: {earliest.claim if earliest else '(unknown)'}\n"
        f"Failure layer: {cls.layer}\n"
        f"Correct response: {cls.summary}\n"
        f"Backtrack target: {cls.backtrack_target}\n"
        "Repair THAT assumption. Invalidate dependent evidence. "
        "Re-run checks from that point downward. "
        "Do NOT weaken the verification mechanism."
    )
    if weaken_blocked:
        guidance += (
            "\nBLOCKED: proposed repair touches the failing detector — "
            "conflict of interest. Preserve verification strength."
        )

    return BacktrackResult(
        classification=cls,
        chain=chain,
        earliest=earliest,
        invalidated_evidence_kinds=invalidated,
        repair_guidance=guidance,
        weaken_blocked=weaken_blocked,
    )


def backtrack_nodes(
    result: BacktrackResult,
    *,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
) -> List[UncertaintyNode]:
    if not result.earliest:
        return []
    return [
        UncertaintyNode(
            title=f"Causal backtrack — repair: {result.earliest.claim[:80]}",
            type=NodeType.CAUSAL_BACKTRACK,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.HIGH if result.weaken_blocked else Tier.MEDIUM,
            summary=result.earliest.claim,
            explanation=result.repair_guidance,
            why_uncertain=(
                "A later check failed; the earliest unsupported parent assumption "
                "must be repaired before dependent evidence is trustworthy."
            ),
            what_could_go_wrong=(
                "Fixing the symptom (or the detector) leaves the root cause intact "
                "and produces false greens."
            ),
            suggested_fix=result.repair_guidance,
            suggested_prompt=result.repair_guidance,
            status=NodeStatus.OPEN,
            area=Area.TESTS,
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            signals={
                "causal_backtrack": True,
                "earliest_id": result.earliest.id,
                "failure_layer": result.classification.layer,
                "weaken_blocked": result.weaken_blocked,
                "verification_blocked": result.weaken_blocked,
            },
        )
    ]


def format_backtrack(result: BacktrackResult) -> str:
    lines = [
        "Causal backtrack:",
        f"  Failure layer: {result.classification.layer}",
        "",
        "  Assumption chain:",
    ]
    for n in result.chain:
        mark = {
            "contradicted": "✗",
            "supported": "✓",
            "repaired": "↻",
            "assumed": "·",
        }.get(n.status, "?")
        arrow = " ← repair here" if result.earliest and n.id == result.earliest.id else ""
        lines.append(f"    [{mark}] {n.claim}{arrow}")
    lines.append("")
    lines.append(result.repair_guidance)
    return "\n".join(lines)


def reopen_on_contradiction(
    nodes: Sequence[UncertaintyNode],
    *,
    new_evidence_summary: str,
    contradicts_signal: str = "",
) -> List[UncertaintyNode]:
    """
    If new evidence conflicts with a resolved node, reopen it and clear resolution.
    """
    reopened: List[UncertaintyNode] = []
    for node in nodes:
        if node.status != NodeStatus.RESOLVED:
            continue
        # Reopen verification/journey/completion nodes when contradicted
        reopen = False
        if contradicts_signal and node.signals.get(contradicts_signal):
            reopen = True
        if node.type in (
            NodeType.FALSE_COMPLETION_RISK,
            NodeType.MISSING_TEST,
            NodeType.REQUIREMENT_GAP,
            NodeType.CAUSAL_BACKTRACK,
        ):
            if new_evidence_summary:
                reopen = True
        if not reopen:
            continue
        node.status = NodeStatus.OPEN
        node.resolved_at = None
        node.signals["reopened"] = True
        node.signals["reopen_reason"] = new_evidence_summary[:400]
        node.explanation = (
            (node.explanation or "")
            + f"\n\nREOPENED: {new_evidence_summary[:400]}"
        )
        reopened.append(node)
    return reopened
