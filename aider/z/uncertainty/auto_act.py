"""
Map high human-worry nodes to automatic next actions (bounded reflects).

Used by the post-edit pipeline before the commit gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .schema import NodeStatus, NodeType, Tier, UncertaintyNode
from .store import UncertaintyStore

# Types the agent can try to fix automatically (reflect loop).
_AUTO_FIXABLE = {
    NodeType.MISSING_TEST,
    NodeType.REQUIREMENT_GAP,
    NodeType.FAILURE_BLIND_SPOT,
    NodeType.EDGE_CASE,
}


@dataclass
class AutoActResult:
    reflect_message: Optional[str] = None
    acted_on: List[UncertaintyNode] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.acted_on is None:
            self.acted_on = []


def default_prompt_for_node(node: UncertaintyNode) -> str:
    """Concrete next-step prompt for a node type (human-worry oriented)."""
    files = ", ".join(node.files_affected[:5]) or "the recent change"
    if node.suggested_prompt:
        base = node.suggested_prompt
    else:
        base = node.suggested_fix or node.summary

    if node.type == NodeType.MISSING_TEST:
        return (
            f"Untested path in {files}. Write a focused automated test for the "
            f"changed behavior (happy path + one failure/edge). Run it. "
            f"Detail: {node.summary}"
        )
    if node.type == NodeType.REQUIREMENT_GAP:
        missing = node.signals.get("missing") or node.signals.get("requirement_text") or node.summary
        return (
            f"Requirement gap ({node.signals.get('requirement_status', 'open')}): "
            f"{missing}. Implement the missing behavior now. Do not expand scope."
        )
    if node.type == NodeType.EDGE_CASE:
        return (
            f"Edge-case blind spot for {files}: {node.summary}. "
            "Add a test or explicit handling for this case."
        )
    if node.type == NodeType.API_ASSUMPTION:
        api = node.signals.get("api") or "the external dependency"
        return (
            f"Unverified assumption about {api} in {files}. "
            "Add a contract/smoke check or adjust code to match a recorded response. "
            f"{base}"
        )
    if node.type == NodeType.HIGH_STAKES:
        return (
            f"High-stakes surface touched ({files}). "
            "Add targeted tests for the auth/payment/data path and re-verify. "
            f"{base}"
        )
    if node.type == NodeType.FAILURE_BLIND_SPOT:
        return (
            f"Failure blind spot in {files}: {node.summary}. "
            "Handle the failure path and add a test that exercises it."
        )
    if node.type == NodeType.FRAGILE_LOGIC:
        return (
            f"Fragile logic in {files}: {node.summary}. "
            "Simplify or add characterization tests before changing further."
        )
    if node.type == NodeType.SHARED_LOGIC:
        return (
            f"Integration ripple around {files}: {node.summary}. "
            "Check callers and run broader tests."
        )
    if node.type == NodeType.PATTERN_INCONSISTENCY:
        return (
            f"Pattern misfit in {files}: {node.summary}. "
            "Align with the canonical peer pattern or document why not."
        )
    return base


def select_auto_act_targets(
    nodes: Sequence[UncertaintyNode],
    *,
    max_targets: int = 2,
) -> List[UncertaintyNode]:
    """Pick High (or Needs Human Review test failures) that are auto-fixable."""
    candidates = []
    for n in nodes:
        if n.status in (NodeStatus.RESOLVED, NodeStatus.IGNORED):
            continue
        if n.type not in _AUTO_FIXABLE:
            continue
        if n.risk_tier == Tier.HIGH or n.status == NodeStatus.NEEDS_HUMAN_REVIEW:
            candidates.append(n)
        elif n.type == NodeType.REQUIREMENT_GAP and n.signals.get("requirement_status") == "Not Addressed":
            candidates.append(n)
    # Prefer verification / tests, then requirement gaps
    order = {
        NodeType.MISSING_TEST: 0,
        NodeType.REQUIREMENT_GAP: 1,
        NodeType.EDGE_CASE: 2,
        NodeType.FAILURE_BLIND_SPOT: 3,
    }
    candidates.sort(key=lambda n: (order.get(n.type, 9), n.title))
    return candidates[:max_targets]


def plan_auto_act(
    store: UncertaintyStore,
    nodes: Sequence[UncertaintyNode],
    *,
    attempts: int = 0,
    max_attempts: int = 1,
) -> AutoActResult:
    """
    If High auto-fixables remain and attempts remain, return a reflect message.
    Marks targeted nodes In Progress.
    """
    if attempts >= max_attempts:
        return AutoActResult()

    targets = select_auto_act_targets(nodes)
    if not targets:
        return AutoActResult()

    prompts = []
    for node in targets:
        store.update_status(node.id, NodeStatus.IN_PROGRESS)
        prompts.append(f"- [{node.type.value}] {default_prompt_for_node(node)}")

    msg = (
        "Z risk auto-act: address these high-priority findings before "
        "claiming completion or committing:\n"
        + "\n".join(prompts)
        + "\nRules: keep changes focused on product behavior only; "
        "do not add meta/policy tests about agent process; "
        "do not invent product commands for internal tooling; "
        "re-run the project's test command after edits."
    )
    return AutoActResult(reflect_message=msg, acted_on=list(targets))
