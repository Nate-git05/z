"""CLI browse / detail / action UI for the uncertainty tree.

Pretty sessions use Rich hierarchy (P2); dumb/non-pretty stay plain text.
Risk/confidence tiers only — no percentages, no emojis.
"""

from __future__ import annotations

from collections import defaultdict
from typing import List, Optional, Sequence, Tuple

from ..theme import ACCENT as _ACCENT_HEX
from ..theme import ANSI_RESET as RESET
from ..theme import TEXT_MUTED as _TEXT_MUTED_HEX
from ..theme import hex_to_ansi_fg
from .actions import apply_action
from .schema import Tier, UncertaintyNode
from .store import UncertaintyStore, sort_nodes
from .tree import SortMode, build_tree, flatten_for_display


# Plain/non-pretty rendering must match the same palette the Rich renderer
# below uses (theme.py) — these used to be separately hand-picked raw ANSI
# constants that had drifted to a different orange than every bordered
# panel elsewhere in the app.
ACCENT = hex_to_ansi_fg(_ACCENT_HEX)
DIM = hex_to_ansi_fg(_TEXT_MUTED_HEX)
BOLD = "\033[1m"

_TIER_MARKERS = {
    Tier.HIGH: "‼",
    Tier.MEDIUM: "▸",
    Tier.LOW: "·",
}
_TIER_ORDER = (Tier.HIGH, Tier.MEDIUM, Tier.LOW)


def _c(enabled: bool, code: str, text: str) -> str:
    if not enabled:
        return text
    return f"{code}{text}{RESET}"


def _gate_tier(node: UncertaintyNode) -> Tier:
    try:
        from .gate import _effective_gate_tier

        return _effective_gate_tier(node)
    except Exception:
        return node.risk_tier or Tier.LOW


def _path_label(node: UncertaintyNode) -> str:
    if node.files_affected:
        return node.files_affected[0]
    if node.area:
        return getattr(node.area, "value", str(node.area))
    return "(no file)"


def rows_for_listing(
    store: UncertaintyStore,
    *,
    mode: SortMode = "risk",
    include_resolved: bool = False,
) -> List[Tuple[str, UncertaintyNode]]:
    """Numbered browse order — risk mode groups by gate-effective tier."""
    nodes = store.list(include_resolved=include_resolved)
    if not nodes:
        return []
    if mode == "risk":
        by_tier: dict[Tier, List[UncertaintyNode]] = defaultdict(list)
        for n in nodes:
            by_tier[_gate_tier(n)].append(n)
        rows: List[Tuple[str, UncertaintyNode]] = []
        for tier in _TIER_ORDER:
            for n in sort_nodes(by_tier.get(tier) or []):
                rows.append((_path_label(n), n))
        return rows
    tree = build_tree(nodes, mode=mode)
    return flatten_for_display(tree, mode=mode)


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
    if node.signals.get("reference_count") is not None:
        lines.append(
            f"Blast radius: refs={node.signals.get('reference_count')} "
            f"threshold={node.signals.get('blast_radius_threshold')}"
        )
    return "\n".join(lines)


def format_summary_line(new_nodes: Sequence[UncertaintyNode], *, verbose: bool = False) -> str:
    """Compact post-edit triage crumb (P2)."""
    if not new_nodes:
        return ""
    try:
        from .gate import _effective_gate_tier

        high = sum(1 for n in new_nodes if _effective_gate_tier(n) == Tier.HIGH)
        med = sum(1 for n in new_nodes if _effective_gate_tier(n) == Tier.MEDIUM)
        low = sum(1 for n in new_nodes if _effective_gate_tier(n) == Tier.LOW)
    except Exception:
        high = sum(1 for n in new_nodes if n.risk_tier.value == "High")
        med = sum(1 for n in new_nodes if n.risk_tier.value == "Medium")
        low = sum(1 for n in new_nodes if n.risk_tier.value == "Low")

    parts: List[str] = []
    if high:
        parts.append(f"{high} High")
    if med:
        parts.append(f"{med} Medium")
    if low and not high and not med:
        parts.append(f"{low} Low")
    elif low and verbose:
        parts.append(f"{low} Low")
    if not parts:
        parts.append(f"{len(new_nodes)} Low")

    line = "Uncertainty · " + " · ".join(parts) + " — /uncertainties"
    if verbose:
        line += f" ({len(new_nodes)} new)"
    return line


def print_summary_line(io, new_nodes: List[UncertaintyNode]) -> None:
    if not new_nodes:
        return
    from aider.z.ux_preamble import ux_verbose

    verbose = ux_verbose(io=io)
    io.tool_output(format_summary_line(new_nodes, verbose=verbose))


def render_tree_listing(
    store: UncertaintyStore,
    *,
    mode: SortMode = "risk",
    color: bool = True,
    include_resolved: bool = False,
) -> str:
    """Plain-text listing (non-pretty / dumb terminals)."""
    rows = rows_for_listing(store, mode=mode, include_resolved=include_resolved)
    if not rows:
        return "No open uncertainty nodes."

    lines = [
        _c(color, BOLD, "Uncertainty")
        + _c(color, DIM, f" · {len(rows)} open · sort={mode}"),
        "",
    ]
    if mode == "risk":
        current_tier: Optional[Tier] = None
        for i, (path, node) in enumerate(rows, start=1):
            tier = _gate_tier(node)
            if tier is not current_tier:
                current_tier = tier
                marker = _TIER_MARKERS.get(tier, "·")
                tier_color = ACCENT if tier in (Tier.HIGH, Tier.MEDIUM) else DIM
                lines.append(
                    _c(color, tier_color, f"{marker} {tier.value}")
                )
            lines.append(f"  {i}. [{path}] {format_collapsed(node, color=color)}")
    else:
        for i, (path, node) in enumerate(rows, start=1):
            lines.append(f"  {i}. [{path}] {format_collapsed(node, color=color)}")
    lines.append("")
    lines.append(
        _c(color, DIM, "Select # · [f]ile [s]ession [r]isk · Enter exits")
    )
    return "\n".join(lines)


def render_tree_rich(
    store: UncertaintyStore,
    console,
    *,
    mode: SortMode = "risk",
    include_resolved: bool = False,
) -> List[Tuple[str, UncertaintyNode]]:
    """Rich tier hierarchy. Returns the numbered row list used for selection."""
    from rich.style import Style
    from rich.text import Text

    from aider.z.theme import ACCENT as THEME_ACCENT
    from aider.z.theme import ACCENT_BRIGHT, TEXT, TEXT_MUTED

    rows = rows_for_listing(store, mode=mode, include_resolved=include_resolved)
    if not rows:
        console.print(Text("No open uncertainty nodes.", style=Style(color=TEXT_MUTED)))
        return []

    header = Text()
    header.append("Uncertainty", style=Style(color=TEXT, bold=True))
    header.append(
        f" · {len(rows)} open · sort={mode}",
        style=Style(color=TEXT_MUTED),
    )
    console.print(header)
    console.print()

    if mode == "risk":
        current_tier: Optional[Tier] = None
        for i, (path, node) in enumerate(rows, start=1):
            tier = _gate_tier(node)
            if tier is not current_tier:
                current_tier = tier
                marker = _TIER_MARKERS.get(tier, "·")
                if tier is Tier.HIGH:
                    tcolor = ACCENT_BRIGHT
                elif tier is Tier.MEDIUM:
                    tcolor = THEME_ACCENT
                else:
                    tcolor = TEXT_MUTED
                tier_line = Text()
                tier_line.append(f"{marker} ", style=Style(color=tcolor, bold=True))
                tier_line.append(tier.value, style=Style(color=tcolor, bold=True))
                console.print(tier_line)
            row = Text()
            row.append(f"  {i}. ", style=Style(color=TEXT_MUTED))
            row.append(f"[{path}] ", style=Style(color=TEXT_MUTED))
            row.append(node.title, style=Style(color=TEXT, bold=True))
            row.append(f"  {node.type.value}", style=Style(color=TEXT_MUTED))
            risk_style = (
                Style(color=ACCENT_BRIGHT)
                if tier is Tier.HIGH
                else Style(color=THEME_ACCENT)
                if tier is Tier.MEDIUM
                else Style(color=TEXT_MUTED)
            )
            row.append(f"  risk={node.risk_tier.value}", style=risk_style)
            console.print(row)
        console.print()
    else:
        for i, (path, node) in enumerate(rows, start=1):
            row = Text()
            row.append(f"  {i}. ", style=Style(color=TEXT_MUTED))
            row.append(f"[{path}] ", style=Style(color=TEXT_MUTED))
            row.append(node.title, style=Style(color=TEXT, bold=True))
            console.print(row)
        console.print()

    console.print(
        Text(
            "Select # · [f]ile [s]ession [r]isk · Enter exits",
            style=Style(color=TEXT_MUTED),
        )
    )
    return rows


def render_detail_rich(node: UncertaintyNode, console) -> None:
    """Rich panel detail for a selected node."""
    from rich.panel import Panel
    from rich.style import Style
    from rich.text import Text

    from aider.z.theme import ACCENT as THEME_ACCENT
    from aider.z.theme import ACCENT_BRIGHT, TEXT, TEXT_MUTED

    gate = _gate_tier(node)
    marker = _TIER_MARKERS.get(gate, "·")
    if gate is Tier.HIGH:
        border = ACCENT_BRIGHT
        tier_color = ACCENT_BRIGHT
    elif gate is Tier.MEDIUM:
        border = THEME_ACCENT
        tier_color = THEME_ACCENT
    else:
        border = TEXT_MUTED
        tier_color = TEXT_MUTED

    title = Text()
    title.append(f"{marker} ", style=Style(color=tier_color, bold=True))
    title.append(node.title, style=Style(color=TEXT, bold=True))
    title.append(f"  · {gate.value}", style=Style(color=tier_color))

    body = Text()
    body.append("Summary\n", style=Style(color=TEXT_MUTED, bold=True))
    body.append((node.summary or "(none)") + "\n", style=Style(color=TEXT))
    body.append("\nType / status\n", style=Style(color=TEXT_MUTED, bold=True))
    body.append(
        f"  {node.type.value} · risk={node.risk_tier.value} · "
        f"confidence={node.confidence_tier.value} · {node.status.value}\n",
        style=Style(color=TEXT),
    )
    if node.files_affected or node.symbols_affected:
        body.append("\nInvolved\n", style=Style(color=TEXT_MUTED, bold=True))
        if node.files_affected:
            body.append(
                "  files: " + ", ".join(node.files_affected[:8]) + "\n",
                style=Style(color=TEXT),
            )
        if node.symbols_affected:
            body.append(
                "  symbols: " + ", ".join(node.symbols_affected[:8]) + "\n",
                style=Style(color=TEXT),
            )
    why = (node.why_uncertain or node.explanation or "").strip()
    if why:
        body.append("\nWhy uncertain\n", style=Style(color=TEXT_MUTED, bold=True))
        # Cap wall-of-text
        clipped = why if len(why) <= 800 else why[:800] + "…"
        body.append(clipped + "\n", style=Style(color=TEXT))
    if node.suggested_fix:
        body.append("\nSuggested fix\n", style=Style(color=TEXT_MUTED, bold=True))
        body.append(node.suggested_fix + "\n", style=Style(color=TEXT))
    body.append("\nActions\n", style=Style(color=TEXT_MUTED, bold=True))
    body.append(
        "  [F]ix  [T]est  [E]xplain  [I]gnore  [C]ustom  [B]ack\n",
        style=Style(color=THEME_ACCENT, bold=True),
    )

    console.print(
        Panel(body, title=title, border_style=Style(color=border), padding=(0, 1))
    )


def _print_listing(io, store: UncertaintyStore, *, mode: SortMode) -> List[Tuple[str, UncertaintyNode]]:
    pretty = bool(getattr(io, "pretty", True))
    if pretty and getattr(io, "console", None) is not None:
        try:
            return render_tree_rich(store, io.console, mode=mode)
        except Exception:
            pass
    listing = render_tree_listing(store, mode=mode, color=pretty)
    io.tool_output(listing)
    return rows_for_listing(store, mode=mode)


def browse_interactive(io, store: UncertaintyStore, *, mode: SortMode = "risk") -> Optional[str]:
    """
    Interactive browse using existing Z/aider IO confirm/prompt patterns.
    Returns an agent follow-up prompt if the user queued an action, else None.
    """
    pretty = bool(getattr(io, "pretty", True))
    if hasattr(io, "no_pretty") and getattr(io, "no_pretty", False):
        pretty = False
    color = pretty

    current_mode: SortMode = mode
    while True:
        rows = _print_listing(io, store, mode=current_mode)
        if not rows:
            return None

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
        if pretty and getattr(io, "console", None) is not None:
            try:
                render_detail_rich(node, io.console)
            except Exception:
                io.tool_output("")
                io.tool_output(format_detail(node, color=color))
                io.tool_output("")
                io.tool_output(
                    "Actions: [F]ix this  [T]est  [E]xplain further  [I]gnore  [C]ustom  [B]ack"
                )
        else:
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
