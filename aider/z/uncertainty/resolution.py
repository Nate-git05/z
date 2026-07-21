"""Resolution contracts and node lifecycle (P1.2)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Sequence, Set

from .schema import NodeStatus, NodeType, UncertaintyNode

NodeLifecycle = Literal["persistent_risk", "temporary_blocker"]


@dataclass
class ResolutionContract:
    node_id: str
    acceptable_evidence: List[str] = field(default_factory=list)
    contradiction_signals: List[str] = field(default_factory=list)
    expires_after_task: bool = True
    source_requirement_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ResolutionContract":
        return cls(
            node_id=data.get("node_id") or "",
            acceptable_evidence=list(data.get("acceptable_evidence") or []),
            contradiction_signals=list(data.get("contradiction_signals") or []),
            expires_after_task=bool(data.get("expires_after_task", True)),
            source_requirement_id=data.get("source_requirement_id"),
        )


# Node types that default to temporary blockers
_TEMPORARY_TYPES = {
    NodeType.MISSING_TEST,  # often task-local when from a blocked run
    NodeType.UNVERIFIABLE_CONFIG,
}

# Explicit temporary: shell approval / test failure style signals in detectors
def default_lifecycle_for_type(node_type: NodeType, *, signals: Optional[dict] = None) -> NodeLifecycle:
    signals = signals or {}
    if signals.get("temporary_blocker") or signals.get("shell_approval_block"):
        return "temporary_blocker"
    if signals.get("test_failure") or signals.get("expires_after_task"):
        return "temporary_blocker"
    # High-stakes / coverage / dependency risks persist
    if node_type in (
        NodeType.HIGH_STAKES,
        NodeType.DEPENDENCY_FABRICATION,
        NodeType.VERIFICATION_INTEGRITY,
        NodeType.ARCHITECTURE_GAP,
        NodeType.CAPABILITY_GAP,
        NodeType.ESTABLISHED_SOLUTION_GAP,
    ):
        return "persistent_risk"
    if node_type in _TEMPORARY_TYPES and signals.get("from_shell"):
        return "temporary_blocker"
    return "persistent_risk"


def contract_for_node(
    node: UncertaintyNode,
    *,
    source_requirement_id: Optional[str] = None,
) -> ResolutionContract:
    """Build a required resolution contract for a newly created node."""
    signals = dict(node.signals or {})
    lifecycle = signals.get("lifecycle") or default_lifecycle_for_type(
        node.type, signals=signals
    )
    expires = lifecycle == "temporary_blocker"

    evidence: List[str] = []
    contradictions: List[str] = []

    if signals.get("shell_approval_block") or signals.get("blocked_command"):
        cmd = str(signals.get("blocked_command") or signals.get("command") or "")
        evidence = [f"command_success:{cmd}"] if cmd else ["command_success"]
        contradictions = [f"command_failed:{cmd}"] if cmd else ["command_failed"]
        expires = True
    elif signals.get("test_failure") or signals.get("test_id") or signals.get(
        "test_name"
    ):
        test_id = str(signals.get("test_id") or signals.get("test_name") or "suite")
        evidence = [f"test_pass:{test_id}"]
        contradictions = [f"test_fail:{test_id}"]
        expires = True
    elif node.type == NodeType.REQUIREMENT_GAP:
        rid = source_requirement_id or signals.get("requirement_id")
        evidence = [f"requirement_addressed:{rid or node.id}"]
        contradictions = [f"requirement_regressed:{rid or node.id}"]
        expires = False
    elif node.type == NodeType.FAILURE_BLIND_SPOT:
        evidence = ["source_inspection", "test_execution"]
        contradictions = ["same_failure_recurs"]
        expires = False
    else:
        evidence = list(signals.get("acceptable_evidence") or ["manual_resolve"])
        contradictions = list(signals.get("contradiction_signals") or [])

    return ResolutionContract(
        node_id=node.id,
        acceptable_evidence=evidence,
        contradiction_signals=contradictions,
        expires_after_task=expires,
        source_requirement_id=source_requirement_id
        or signals.get("requirement_id")
        or signals.get("source_requirement_id"),
    )


@dataclass
class BlockerExplanation:
    node_id: str
    blocks: bool
    has_current_source_requirement: bool
    has_unsatisfied_contract: bool
    current_tree_relevant: bool
    detector_fresh: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def explain_blocker(
    node: UncertaintyNode,
    *,
    active_requirement_ids: Optional[Set[str]] = None,
    session_evidence: Optional[Sequence[str]] = None,
    current_task_id: Optional[str] = None,
) -> BlockerExplanation:
    """
    Four-condition check for whether a node may block completion (P1.2).
    """
    active_requirement_ids = active_requirement_ids or set()
    session_evidence = list(session_evidence or [])
    signals = dict(node.signals or {})
    contract_raw = signals.get("resolution_contract") or {}
    contract = (
        ResolutionContract.from_dict(contract_raw)
        if contract_raw
        else contract_for_node(node)
    )

    src = contract.source_requirement_id
    has_src = bool(src) and (not active_requirement_ids or src in active_requirement_ids)
    # Nodes without a source requirement can still block if they're integrity/high-stakes
    if not src and node.type in (
        NodeType.VERIFICATION_INTEGRITY,
        NodeType.HIGH_STAKES,
        NodeType.FALSE_COMPLETION_RISK,
    ):
        has_src = True

    satisfied = any(
        any(ev.lower() in (e or "").lower() for e in session_evidence)
        for ev in contract.acceptable_evidence
    )
    # Also match command_success:<cmd> against "command_ok:<cmd>"
    if not satisfied:
        for ev in contract.acceptable_evidence:
            if ev.startswith("command_success:"):
                cmd = ev.split(":", 1)[1]
                if any(
                    re.search(rf"command_ok:\s*{re.escape(cmd)}", e, re.I)
                    or (cmd and cmd in e and "ok" in e.lower())
                    for e in session_evidence
                ):
                    satisfied = True
                    break
            if ev.startswith("test_pass:"):
                tid = ev.split(":", 1)[1]
                if any(
                    re.search(rf"test_ok:\s*{re.escape(tid)}", e, re.I)
                    or (tid != "suite" and tid in e and "pass" in e.lower())
                    for e in session_evidence
                ):
                    satisfied = True
                    break

    has_unsatisfied = not satisfied and node.status not in (
        NodeStatus.RESOLVED,
        NodeStatus.IGNORED,
    )

    # Expiry: temporary blockers from other tasks are not relevant
    lifecycle = signals.get("lifecycle") or (
        "temporary_blocker" if contract.expires_after_task else "persistent_risk"
    )
    current_tree_relevant = True
    if contract.expires_after_task and current_task_id and node.task_id:
        if node.task_id != current_task_id:
            current_tree_relevant = False
    if signals.get("stale"):
        current_tree_relevant = False

    detector_fresh = not bool(signals.get("stale") or signals.get("detector_stale"))

    reasons = []
    if not has_src:
        reasons.append("no current source_requirement_id")
    if not has_unsatisfied:
        reasons.append("contract already satisfied or node closed")
    if not current_tree_relevant:
        reasons.append("not relevant to current tree/task (expired or stale)")
    if not detector_fresh:
        reasons.append("detector result stale — needs re-check")

    blocks = bool(
        has_src
        and has_unsatisfied
        and current_tree_relevant
        and detector_fresh
        and node.status not in (NodeStatus.RESOLVED, NodeStatus.IGNORED)
    )
    if blocks:
        reasons = [
            "has current source requirement",
            "unsatisfied resolution contract",
            "current-tree relevant",
            "detector result fresh",
        ]

    return BlockerExplanation(
        node_id=node.id,
        blocks=blocks,
        has_current_source_requirement=has_src,
        has_unsatisfied_contract=has_unsatisfied,
        current_tree_relevant=current_tree_relevant,
        detector_fresh=detector_fresh,
        reasons=reasons,
    )


def attach_contract_to_node(
    node: UncertaintyNode,
    *,
    source_requirement_id: Optional[str] = None,
) -> UncertaintyNode:
    """Stamp lifecycle + resolution contract onto node.signals (required at create)."""
    contract = contract_for_node(node, source_requirement_id=source_requirement_id)
    lifecycle = default_lifecycle_for_type(node.type, signals=node.signals)
    if contract.expires_after_task:
        lifecycle = "temporary_blocker"
    node.signals = dict(node.signals or {})
    node.signals["resolution_contract"] = contract.to_dict()
    node.signals["lifecycle"] = lifecycle
    node.signals["expires_after_task"] = contract.expires_after_task
    return node


def try_auto_resolve(
    node: UncertaintyNode,
    *,
    session_evidence: Sequence[str],
) -> bool:
    """
    Return True if the node should be marked resolved given session evidence.
    Does not mutate storage — caller updates status.
    """
    if node.status in (NodeStatus.RESOLVED, NodeStatus.IGNORED):
        return False
    expl = explain_blocker(
        node,
        session_evidence=session_evidence,
        active_requirement_ids=None,
    )
    # Auto-resolve when contract is satisfied (has_unsatisfied_contract False
    # because evidence matched), and node was open.
    signals = dict(node.signals or {})
    contract_raw = signals.get("resolution_contract") or {}
    if not contract_raw:
        return False
    contract = ResolutionContract.from_dict(contract_raw)
    for ev in contract.acceptable_evidence:
        for e in session_evidence:
            el = (e or "").lower()
            if ev.lower() in el:
                return True
            if ev.startswith("command_success:"):
                cmd = ev.split(":", 1)[1]
                if cmd and cmd in e and ("ok" in el or "success" in el or "ran" in el):
                    return True
            if ev.startswith("test_pass:"):
                tid = ev.split(":", 1)[1]
                if (tid == "suite" or tid in e) and ("pass" in el or "ok" in el):
                    return True
    return False


def try_reopen(
    node: UncertaintyNode,
    *,
    session_evidence: Sequence[str],
) -> bool:
    """Return True if a resolved node should reopen due to contradiction signals."""
    if node.status != NodeStatus.RESOLVED:
        return False
    signals = dict(node.signals or {})
    contract_raw = signals.get("resolution_contract") or {}
    if not contract_raw:
        return False
    contract = ResolutionContract.from_dict(contract_raw)
    for sig in contract.contradiction_signals:
        for e in session_evidence:
            if sig.lower() in (e or "").lower():
                return True
    return False


def filter_active_blockers(
    nodes: Sequence[UncertaintyNode],
    *,
    active_requirement_ids: Optional[Set[str]] = None,
    session_evidence: Optional[Sequence[str]] = None,
    current_task_id: Optional[str] = None,
    include_carried_persistent: bool = True,
) -> List[UncertaintyNode]:
    """Nodes that currently block completion under the four-condition check."""
    out: List[UncertaintyNode] = []
    for n in nodes:
        signals = dict(n.signals or {})
        lifecycle = signals.get("lifecycle") or "persistent_risk"
        # Temporary blockers never cross sessions via merge relevance
        if lifecycle == "temporary_blocker" and current_task_id and n.task_id:
            if n.task_id != current_task_id:
                continue
        if (
            lifecycle == "persistent_risk"
            and signals.get("carried_over")
            and not include_carried_persistent
        ):
            continue
        expl = explain_blocker(
            n,
            active_requirement_ids=active_requirement_ids,
            session_evidence=session_evidence,
            current_task_id=current_task_id,
        )
        if expl.blocks:
            n.signals = dict(signals)
            n.signals["blocker_explanation"] = expl.to_dict()
            out.append(n)
    return out


def expire_task_local_nodes(
    nodes: Sequence[UncertaintyNode],
    *,
    task_id: str,
) -> List[UncertaintyNode]:
    """Mark temporary blockers for a finished task as resolved/expired."""
    expired = []
    now = datetime.now(timezone.utc).isoformat()
    for n in nodes:
        signals = dict(n.signals or {})
        if not signals.get("expires_after_task") and signals.get("lifecycle") != "temporary_blocker":
            continue
        if n.task_id and n.task_id != task_id:
            continue
        if n.status in (NodeStatus.RESOLVED, NodeStatus.IGNORED):
            continue
        n.status = NodeStatus.RESOLVED
        n.resolved_at = now
        n.signals = signals
        n.signals["expired_with_task"] = True
        expired.append(n)
    return expired
