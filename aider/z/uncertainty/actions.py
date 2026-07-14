"""Follow-up actions from an uncertainty node detail view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .schema import NodeStatus, UncertaintyNode
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

    Fix this → In Progress + suggested_prompt (or fix-oriented prompt)
    Add a test → In Progress + test prompt
    Explain further → stays Open / Needs Human Review + explain prompt
    Ignore → Ignored
    Custom → In Progress + custom text
    """
    action = (action or "").strip().lower()
    if action in ("fix", "fix this", "f"):
        prompt = node.suggested_prompt or (
            f"Fix the issue described in uncertainty node '{node.title}': {node.summary}"
        )
        store.update_status(node.id, NodeStatus.IN_PROGRESS)
        return ActionResult(NodeStatus.IN_PROGRESS, prompt=prompt, message="Marked In Progress; queued fix.")

    if action in ("test", "add a test", "add test", "t"):
        tests = "; ".join(node.suggested_tests) if node.suggested_tests else node.summary
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
