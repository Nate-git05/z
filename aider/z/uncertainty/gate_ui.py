"""Commit-gate visualization — renders a GateResult as a legible panel.

Pure rendering only: never calls commit_block_ledger.append_block or emits
the "gate/commit_blocked" IPC event — those side effects stay in gate.py's
prepare_commit/emit_commit_blocked. This module only formats what a
GateResult already carries, reusing the codebase's established visual
language (aider/z/escalation.py's bordered panel, aider/z/uncertainty/ui.py's
tier markers/colors) rather than inventing a new one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from ..escalation import _panel_width
from ..theme import ACCENT, ACCENT_BRIGHT, TEXT, TEXT_MUTED
from .gate import format_commit_blocked_message
from .schema import Tier

if TYPE_CHECKING:
    from .gate import GateResult

_TIER_MARKERS = {Tier.HIGH: "‼", Tier.MEDIUM: "▸"}


def _node_line(node, *, marker: str, color: str) -> Text:
    line = Text()
    line.append(f"{marker} ", style=Style(color=color, bold=True))
    line.append(node.title, style=Style(color=color, bold=True))
    summary = (node.summary or "").strip()
    if summary:
        if len(summary) > 160:
            summary = summary[:159] + "…"
        line.append(f"\n    {summary}", style=Style(color=TEXT))
    return line


def _verification_line(result: "GateResult") -> Optional[str]:
    rec = result.verification
    if not rec or not getattr(rec, "ran", False):
        return None
    state = getattr(rec.state, "value", str(rec.state))
    parts = [f"Verification: {state}"]
    if rec.tests_discovered is not None:
        parts.append(
            f"{rec.tests_passed or 0}/{rec.tests_discovered} tests passed"
        )
    return " · ".join(parts)


def render_commit_gate(
    result: "GateResult",
    *,
    io,
    dirty_count: Optional[int] = None,
) -> None:
    """Render a GateResult as a structured commit-gate panel.

    Called from aider/commands.py's raw_cmd_commit for both the blocked
    case (replacing the old plain-text message) and, newly, the clear case
    (previously silent) — so the gate's decision is always legible, not
    just when it blocks.
    """
    console = getattr(io, "console", None) or Console()
    pretty = bool(getattr(io, "pretty", True))

    if result.allow_commit:
        _render_clear(result, io=io, console=console, pretty=pretty)
    else:
        _render_blocked(
            result, io=io, console=console, pretty=pretty, dirty_count=dirty_count
        )


def _escape_hatch_text(result: "GateResult", *, dirty_count: Optional[int]) -> str:
    if getattr(result, "block_ui_emitted", False) and result.block_message:
        return result.block_message
    reason = result.reason or "Resolve high-risk issues first."
    return format_commit_blocked_message(reason, dirty_count=dirty_count)


def _render_blocked(
    result: "GateResult",
    *,
    io,
    console: Console,
    pretty: bool,
    dirty_count: Optional[int],
) -> None:
    high = list(result.blocked_high or [])
    medium = list(result.needs_ack_medium or [])
    escape_text = _escape_hatch_text(result, dirty_count=dirty_count)

    if not pretty:
        io.tool_output("=" * 40)
        io.tool_output("Z verify gate: commit blocked")
        io.tool_output(f"High {len(high)} · Medium (needs ack) {len(medium)}")
        for node in high:
            io.tool_output(f"  ‼ [High] {node.title}")
        for node in medium:
            io.tool_output(f"  ▸ [Medium] {node.title}")
        vline = _verification_line(result)
        if vline:
            io.tool_output(vline)
        io.tool_output(escape_text)
        io.tool_output("=" * 40)
        return

    title = Text()
    title.append("⚠ ", style=Style(color=ACCENT_BRIGHT, bold=True))
    title.append("Commit blocked by verify gate", style=Style(color=ACCENT, bold=True))

    body = Text(overflow="fold", no_wrap=False)
    counts = Text()
    counts.append("High ", style=Style(color=TEXT_MUTED))
    counts.append(str(len(high)), style=Style(color=ACCENT_BRIGHT, bold=True))
    counts.append("  ·  Medium (needs ack) ", style=Style(color=TEXT_MUTED))
    counts.append(str(len(medium)), style=Style(color=ACCENT, bold=True))
    body.append(counts)
    body.append("\n")

    if high:
        body.append("\nBlocked — High\n", style=Style(color=ACCENT_BRIGHT, bold=True))
        for node in high:
            body.append(_node_line(node, marker="‼", color=ACCENT_BRIGHT))
            body.append("\n")
    if medium:
        body.append(
            "\nNeeds acknowledgment — Medium\n", style=Style(color=ACCENT, bold=True)
        )
        for node in medium:
            body.append(_node_line(node, marker="▸", color=ACCENT))
            body.append("\n")

    vline = _verification_line(result)
    if vline:
        body.append(f"\n{vline}\n", style=Style(color=TEXT_MUTED))

    body.append(f"\n{escape_text}", style=Style(color=TEXT_MUTED))

    console.print(
        Panel(
            body,
            title=title,
            border_style=Style(color=ACCENT),
            padding=(1, 2),
            subtitle=Text(
                "commit did not happen", style=Style(color=TEXT_MUTED, italic=True)
            ),
            subtitle_align="right",
            width=_panel_width(console),
        )
    )


def _render_clear(
    result: "GateResult",
    *,
    io,
    console: Console,
    pretty: bool,
) -> None:
    acknowledged = list(result.acknowledged_medium or [])
    vline = _verification_line(result) or "Verification: not run"

    if not pretty:
        io.tool_output(f"Commit gate: clear — proceeding. {vline}")
        for node in acknowledged:
            io.tool_output(f"  ▸ acknowledged: {node.title}")
        return

    title = Text()
    title.append("✓ ", style=Style(color=TEXT, bold=True))
    title.append("Commit gate clear", style=Style(color=TEXT, bold=True))

    body = Text(overflow="fold", no_wrap=False)
    body.append("High 0 · Medium 0 blocking\n", style=Style(color=TEXT_MUTED))
    body.append(vline, style=Style(color=TEXT_MUTED))
    if acknowledged:
        body.append("\n\nAcknowledged\n", style=Style(color=TEXT))
        for node in acknowledged:
            body.append("  ▸ ", style=Style(color=ACCENT))
            body.append(node.title + "\n", style=Style(color=TEXT))

    console.print(
        Panel(
            body,
            title=title,
            border_style=Style(color=TEXT_MUTED),
            padding=(1, 2),
            subtitle=Text(
                "proceeding to commit", style=Style(color=TEXT_MUTED, italic=True)
            ),
            subtitle_align="right",
            width=_panel_width(console),
        )
    )
