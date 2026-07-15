"""Follow-up actions from an uncertainty node detail view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .auto_act import default_prompt_for_node
from .schema import NodeStatus, NodeType, UncertaintyNode
from .store import UncertaintyStore


@dataclass
class ActionResult:
    status: NodeStatus
    prompt: Optional[str] = None  # message to send to the agent, if any
    message: str = ""


ACTIONS = ("fix", "test", "explain", "ignore", "custom")


def apply_action(
    store: UncertaintyStore,
    node: UncertaintyNode,
    action: str,
    *,
    custom_text: str = "",
) -> ActionResult:
    """
    Map user follow-up to status + optional agent prompt.

    Fix this → In Progress + type-aware human prompt
    Add a test → In Progress + test prompt
    Explain further → Needs Human Review + explain prompt
    Ignore → Ignored (does not clear High for the commit gate)
    Custom → In Progress + custom text
    """
    action = (action or "").strip().lower()
    if action in ("fix", "fix this", "f"):
        prompt = default_prompt_for_node(node)
        store.update_status(node.id, NodeStatus.IN_PROGRESS)
        return ActionResult(NodeStatus.IN_PROGRESS, prompt=prompt, message="Marked In Progress; queued fix.")

    if action in ("test", "add a test", "add test", "t"):
        tests = "; ".join(node.suggested_tests) if node.suggested_tests else node.summary
        if node.type == NodeType.MISSING_TEST:
            prompt = default_prompt_for_node(node)
        else:
            prompt = (
                f"Add tests for uncertainty '{node.title}' affecting "
                f"{', '.join(node.files_affected[:5]) or 'the recent change'}. "
                f"Recommended: {tests}"
            )
        store.update_status(node.id, NodeStatus.IN_PROGRESS)
        return ActionResult(NodeStatus.IN_PROGRESS, prompt=prompt, message="Marked In Progress; queued test addition.")

    if action in ("explain", "explain further", "e"):
        prompt = (
            f"Explain further the uncertainty '{node.title}'. "
            f"Why uncertain: {node.why_uncertain}. "
            f"What could go wrong: {node.what_could_go_wrong}. "
            f"Expand on: {node.explanation}"
        )
        store.update_status(node.id, NodeStatus.NEEDS_HUMAN_REVIEW)
        return ActionResult(
            NodeStatus.NEEDS_HUMAN_REVIEW,
            prompt=prompt,
            message="Marked Needs Human Review; queued explanation.",
        )

    if action in ("ignore", "i"):
        store.update_status(node.id, NodeStatus.IGNORED)
        return ActionResult(NodeStatus.IGNORED, prompt=None, message="Marked Ignored.")

    if action in ("custom", "c") or custom_text:
        text = custom_text.strip() or action
        scope = ", ".join(node.files_affected[:5] + node.symbols_affected[:5])
        prompt = f"{text}\n\n(Context: uncertainty '{node.title}' — {node.summary}. Scope: {scope})"
        store.update_status(node.id, NodeStatus.IN_PROGRESS)
        return ActionResult(NodeStatus.IN_PROGRESS, prompt=prompt, message="Marked In Progress; queued custom follow-up.")

    if action in ("resolve", "resolved", "done", "r"):
        store.update_status(node.id, NodeStatus.RESOLVED)
        return ActionResult(NodeStatus.RESOLVED, message="Marked Resolved.")

    return ActionResult(node.status, message=f"Unknown action '{action}'. Use fix/test/explain/ignore/custom.")
