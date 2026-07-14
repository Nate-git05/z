"""CLI browse / detail / action UI for the uncertainty tree.

Plain text risk/confidence tiers only — no percentages, no emojis.
"""

from __future__ import annotations

from typing import List, Optional

from .actions import apply_action
from .schema import UncertaintyNode
from .store import UncertaintyStore, sort_nodes
from .tree import SortMode, build_tree, flatten_for_display


ACCENT = "\033[38;2;201;106;43m"  # burnt orange #C96A2B
DIM = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _c(enabled: bool, code: str, text: str) -> str:
    if not enabled:
        return text
    return f"{code}{text}{RESET}"


def format_collapsed(node: UncertaintyNode, *, color: bool = True) -> str:
    """Collapsed view: title, type, risk_tier only."""
    risk = f"risk={node.risk_tier.value}"
    typ = node.type.value
    return (
        f"{_c(color, BOLD, node.title)}  "
        f"{_c(color, DIM, typ)}  "
        f"{_c(color, ACCENT, risk)}"
    )


def format_detail(node: UncertaintyNode, *, color: bool = True) -> str:
    lines = [
        _c(color, BOLD, node.title),
        f"Type: {node.type.value}",
        f"Risk: {node.risk_tier.value}",
        f"Confidence: {node.confidence_tier.value}",
        f"Status: {node.status.value}",
        f"Area: {node.area.value}",
        "",
        f"Summary: {node.summary}",
        "",
        "Explanation:",
        node.explanation or "(none)",
        "",
        f"Files: {', '.join(node.files_affected) or '(none)'}",
        f"Symbols: {', '.join(node.symbols_affected) or '(none)'}",
        "",
        f"Why uncertain: {node.why_uncertain or '(none)'}",
        f"What could go wrong: {node.what_could_go_wrong or '(none)'}",
        f"Suggested fix: {node.suggested_fix or '(none)'}",
        f"Suggested tests: {'; '.join(node.suggested_tests) or '(none)'}",
        f"Suggested prompt: {node.suggested_prompt or '(none)'}",
        "",
        f"Created: {node.created_at}",
        f"Resolved: {node.resolved_at or '—'}",
    ]
    if node.created_by_user or node.created_by_session:
        lines.append(
            f"Created by: user={node.created_by_user or '—'} session={node.created_by_session or '—'}"
        )
    if node.task_title:
        lines.append(f"Task: {node.task_title}")
    # Include blast radius / threshold when present
    if node.signals.get("reference_count") is not None:
        lines.append(
            f"Blast radius: refs={node.signals.get('reference_count')} "
            f"threshold={node.signals.get('blast_radius_threshold')}"
        )
    return "\n".join(lines)


def render_tree_listing(
    store: UncertaintyStore,
    *,
    mode: SortMode = "risk",
    color: bool = True,
    include_resolved: bool = False,
) -> str:
    nodes = store.list(include_resolved=include_resolved)
    if not nodes:
        return "No open uncertainty nodes."
    tree = build_tree(nodes, mode=mode)
    rows = flatten_for_display(tree, mode=mode)
    lines = [
        _c(color, BOLD, "Uncertainty tree")
        + _c(color, DIM, f"  (sort={mode}; risk first by default)"),
        "",
    ]
    for i, (path, node) in enumerate(rows, start=1):
        lines.append(f"  {i}. [{path}] {format_collapsed(node, color=color)}")
    lines.append("")
    lines.append(_c(color, DIM, "Select a number for detail, or /uncertainties <n> for actions."))
    return "\n".join(lines)


def browse_interactive(io, store: UncertaintyStore, *, mode: SortMode = "risk") -> Optional[str]:
    """
    Interactive browse using existing Z/aider IO confirm/prompt patterns.
    Returns an agent follow-up prompt if the user queued an action, else None.
    """
    color = not getattr(io, "pretty", True) is False
    # Always try color unless explicitly dumb
    if hasattr(io, "no_pretty") and getattr(io, "no_pretty", False):
        color = False

    current_mode: SortMode = mode
    while True:
        listing = render_tree_listing(store, mode=current_mode, color=color)
        io.tool_output(listing)
        nodes = store.list(include_resolved=False)
        if not nodes:
            return None

        # Flatten in same order as listing
        tree = build_tree(nodes, mode=current_mode)
        rows = flatten_for_display(tree, mode=current_mode)

        choice = io.prompt_ask(
            "Node #, [f]ile sort, [s]ession sort, [r]isk sort, or Enter to exit",
            default="",
        ).strip().lower()

        if not choice:
            return None
        if choice in ("f", "file"):
            current_mode = "file"
            continue
        if choice in ("s", "session"):
            current_mode = "session"
            continue
        if choice in ("r", "risk"):
            current_mode = "risk"
            continue

        try:
            idx = int(choice)
        except ValueError:
            io.tool_warning("Enter a node number or f/s/r.")
            continue

        if idx < 1 or idx > len(rows):
            io.tool_warning("Out of range.")
            continue

        node = rows[idx - 1][1]
        io.tool_output("")
        io.tool_output(format_detail(node, color=color))
        io.tool_output("")
        io.tool_output(
            "Actions: [F]ix this  [T]est  [E]xplain further  [I]gnore  [C]ustom  [B]ack"
        )
        act = io.prompt_ask("Action", default="b").strip().lower()
        if act in ("b", "back", ""):
            continue
        custom = ""
        if act in ("c", "custom"):
            custom = io.prompt_ask("Custom follow-up").strip()
        result = apply_action(store, node, act, custom_text=custom)
        io.tool_output(result.message)
        if result.prompt:
            return result.prompt
    return None


def print_summary_line(io, new_nodes: List[UncertaintyNode]) -> None:
    if not new_nodes:
        return
    high = sum(1 for n in new_nodes if n.risk_tier.value == "High")
    med = sum(1 for n in new_nodes if n.risk_tier.value == "Medium")
    io.tool_output(
        f"Uncertainty tree: {len(new_nodes)} new node(s) "
        f"(risk High={high} Medium={med}). Use /uncertainties to review."
    )
