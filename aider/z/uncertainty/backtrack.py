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

import re
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
    earliest_selected_by: str = "rule"  # "rule" | "model"

    def to_dict(self) -> dict:
        return {
            "classification": self.classification.to_dict(),
            "chain": [c.to_dict() for c in self.chain],
            "earliest": self.earliest.to_dict() if self.earliest else None,
            "invalidated_evidence_kinds": list(self.invalidated_evidence_kinds),
            "repair_guidance": self.repair_guidance,
            "weaken_blocked": self.weaken_blocked,
            "earliest_selected_by": self.earliest_selected_by,
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


def _backtrack_classify_enabled() -> bool:
    """Escape hatch: Z_BACKTRACK_CLASSIFY=0 disables the model override,
    keeping the deterministic chain-walk's pick (today's behavior)."""
    import os

    raw = (os.environ.get("Z_BACKTRACK_CLASSIFY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _backtrack_classify_timeout() -> float:
    """Z_BACKTRACK_CLASSIFY_TIMEOUT seconds (default 6.0). Only fires on the
    prepare_commit safety-net branch (already a failure), not the hot
    per-turn path, so a longer budget than task_mode's 3.0 is fine."""
    import os

    raw = os.environ.get("Z_BACKTRACK_CLASSIFY_TIMEOUT", "6.0")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 6.0
    return val if val > 0 else 6.0


_BACKTRACK_SYSTEM_PROMPT = (
    "You are diagnosing a verification failure by causal backtracking. "
    "Below is a chain of assumptions ordered from EARLIEST (root prerequisite) "
    "to LATEST (end-to-end journey), each annotated with a status derived from "
    "evidence freshness:\n"
    "  assumed      - never checked\n"
    "  supported    - fresh passing evidence exists for this assumption\n"
    "  contradicted - this is where/why the failure was detected\n"
    "A 'supported' status can still be wrong if the raw failure output below "
    "suggests the freshness check is misleading. Pick the EARLIEST node in "
    "the chain whose assumption is actually false or unverified, given the "
    "failure classification and raw output. Only choose from the candidate "
    "ids listed. Respond with EXACTLY ONE WORD: the node id, lowercase, "
    "nothing else."
)


def _select_earliest_via_model(
    *,
    chain: List[AssumptionNode],
    candidate_ids: List[str],
    cls: FailureClassification,
    output: str,
    error: str,
    classifier_model,
) -> Optional[str]:
    """One-shot weak-model override of the deterministic earliest-node pick.

    Returns a validated node id from ``candidate_ids``, or None on ANY
    failure/timeout/disabled/unparseable/invalid output — caller keeps the
    rule-based pick. Never raises.
    """
    if classifier_model is None or not _backtrack_classify_enabled():
        return None
    if len(candidate_ids) <= 1:
        return None  # nothing to choose between

    candidates = set(candidate_ids)
    by_id = {n.id: n for n in chain}
    lines = [
        f"  [{by_id[nid].status}] {nid}: {by_id[nid].claim}"
        for nid in candidate_ids
        if nid in by_id
    ]
    excerpt = "\n".join(p for p in (error, output) if p)[-2000:]
    user_content = (
        f"Candidate node ids (earliest first): {', '.join(candidate_ids)}\n\n"
        "Chain:\n" + "\n".join(lines) + "\n\n"
        f"Failure layer: {cls.layer}\n"
        f"Classification summary: {cls.summary}\n"
        f"Backtrack target hint: {cls.backtrack_target}\n\n"
        f"Raw failure excerpt:\n{excerpt}"
    )
    messages = [
        {"role": "system", "content": _BACKTRACK_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    from aider.z.latency import join_future, submit_background

    def _call():
        return classifier_model.simple_send_with_retries(messages)

    try:
        fut = submit_background(_call)
    except Exception:
        return None
    raw = join_future(fut, timeout=_backtrack_classify_timeout())
    if not raw or not isinstance(raw, str):
        return None

    for word in re.findall(r"[a-z_]+", raw.strip().lower()):
        if word in candidates:
            return word
    return None


def backtrack_failure(
    *,
    output: str = "",
    error: str = "",
    command: str = "",
    exit_code: Optional[int] = None,
    failure_kind: str = "",
    ledger: Optional[EvidenceLedger] = None,
    proposed_repair_touches_detector: bool = False,
    classifier_model=None,
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
    visited_ids: List[str] = [target.id]
    while cursor and cursor.parent_id:
        parent = by_id.get(cursor.parent_id)
        if not parent:
            break
        visited_ids.append(parent.id)
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

    earliest_selected_by = "rule"
    if classifier_model is not None:
        picked_id = _select_earliest_via_model(
            chain=chain,
            candidate_ids=visited_ids,
            cls=cls,
            output=output,
            error=error,
            classifier_model=classifier_model,
        )
        if picked_id and picked_id != earliest.id and picked_id in by_id:
            earliest = by_id[picked_id]
            earliest.status = "contradicted"
            earliest_selected_by = "model"

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
        earliest_selected_by=earliest_selected_by,
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
                "earliest_selected_by": result.earliest_selected_by,
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
