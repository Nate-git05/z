"""Escalation prompt — visually distinct orange-bordered ask when Z needs the user."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from .theme import ACCENT, ACCENT_BRIGHT, TEXT, TEXT_MUTED


def _panel_width(console: Console) -> int | None:
    """Fit the panel to the live terminal width so wrap matches the TTY."""
    try:
        w = int(getattr(console, "width", 0) or 0)
    except Exception:
        w = 0
    if w < 40:
        return None
    return max(40, w - 1)


def render_escalation(
    question: str,
    *,
    console: Console | None = None,
    context: str | None = None,
    options: list[str] | None = None,
    pretty: bool = True,
    accent_context: bool = False,
) -> None:
    """
    Render an escalation prompt as an orange-bordered box.

    Distinct from normal tool output so the user immediately sees that
    attention is required. Compact plan/context body uses off-white by default;
    pass ``accent_context=True`` for View-full-plan (developer asked for orange).
    """
    console = console or Console()

    if not pretty:
        print("=" * 40)
        print("Z needs your input")
        print(question)
        if context:
            print(context)
        if options:
            for opt in options:
                print(f"  - {opt}")
        print("=" * 40)
        return

    title = Text()
    title.append("⚠ ", style=Style(color=ACCENT_BRIGHT, bold=True))
    title.append("Z needs your input", style=Style(color=ACCENT, bold=True))

    # Soft-fold long drift/plan lines so they wrap with the panel, not the CLI prompt
    body = Text(overflow="fold", no_wrap=False)
    body.append((question or "").strip() + "\n", style=Style(color=TEXT))
    if context:
        body.append("\n", style=Style(color=TEXT))
        ctx_style = Style(color=ACCENT) if accent_context else Style(color=TEXT_MUTED)
        body.append((context or "").strip() + "\n", style=ctx_style)
    if options:
        body.append("\n", style=Style(color=TEXT))
        for opt in options:
            body.append("  ▸ ", style=Style(color=ACCENT))
            # Option labels stay accent so Y/N/C/V affordances read as part of the ask
            body.append(opt + "\n", style=Style(color=ACCENT_BRIGHT))

    console.print(
        Panel(
            body,
            title=title,
            border_style=Style(color=ACCENT),
            padding=(1, 2),
            subtitle=Text("awaiting reply", style=Style(color=TEXT_MUTED, italic=True)),
            subtitle_align="right",
            width=_panel_width(console),
        )
    )


def escalate_ask(io, question: str, *, context: str | None = None, default: str = "") -> str:
    """
    Show an escalation panel, then collect a reply via Aider's prompt_ask.
    """
    console = getattr(io, "console", None) or Console()
    pretty = getattr(io, "pretty", True)
    render_escalation(question, console=console, context=context, pretty=pretty)
    if hasattr(io, "prompt_ask"):
        # Short prompt — long text already in the panel (avoids SIGWINCH garble)
        return io.prompt_ask("Your reply", default=default)
    return input("Your reply: ")
