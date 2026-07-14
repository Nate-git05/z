"""Z startup banner — wordmark, idle mascot, and model/connection status."""

from __future__ import annotations

from rich.console import Console
from rich.style import Style
from rich.text import Text

from .mascot import idle_mascot_lines
from .theme import ACCENT, TEXT, TEXT_DIM, TEXT_MUTED

# Minimal Z wordmark — orange accent only
WORDMARK = [
    r"███████╗",
    r"╚══███╔╝",
    r"  ███╔╝ ",
    r" ███╔╝  ",
    r"███████╗",
    r"╚══════╝",
]

WORDMARK_SIMPLE = [
    "███████",
    "     █ ",
    "    █  ",
    "   █   ",
    "  █    ",
    "███████",
]


def _wordmark_lines(simple: bool = False) -> list[str]:
    return list(WORDMARK_SIMPLE if simple else WORDMARK)


def render_startup_banner(
    console: Console | None = None,
    *,
    version: str = "",
    model_line: str = "",
    status_lines: list[str] | None = None,
    pretty: bool = True,
) -> None:
    """
    Print the Z startup banner.

    Layout:
        [orange Z wordmark]   [idle orange mascot]
        model / connection status in white/gray
        remaining announcement lines
    """
    console = console or Console()
    status_lines = status_lines or []

    if not pretty:
        print(f"Z {version}".strip())
        if model_line:
            print(model_line)
        for line in status_lines:
            print(line)
        return

    accent = Style(color=ACCENT, bold=True)
    text_style = Style(color=TEXT)
    dim_style = Style(color=TEXT_DIM)
    muted_style = Style(color=TEXT_MUTED)

    mark = _wordmark_lines()
    mascot = idle_mascot_lines()

    # Vertically align wordmark + mascot
    mark_h = len(mark)
    mascot_h = len(mascot)
    top_pad = max(0, (mark_h - mascot_h) // 2)
    bottom_pad = max(0, mark_h - mascot_h - top_pad)
    padded_mascot = ([""] * top_pad) + mascot + ([""] * bottom_pad)

    brand_lines = Text()
    for i, mark_line in enumerate(mark):
        brand_lines.append(mark_line, style=accent)
        brand_lines.append("   ", style=text_style)
        m_line = padded_mascot[i] if i < len(padded_mascot) else ""
        brand_lines.append(m_line, style=accent)
        brand_lines.append("\n")

    header = Text()
    title = f"Z {version}".strip() if version else "Z"
    header.append(title, style=accent)
    header.append("  coding agent\n", style=muted_style)

    body = Text()
    if model_line:
        body.append(model_line + "\n", style=text_style)
    for line in status_lines:
        body.append(line + "\n", style=dim_style)

    console.print(header, end="")
    console.print(brand_lines, end="")
    if model_line or status_lines:
        console.print(body, end="")
    console.print()


def render_header_rule(console: Console | None = None, pretty: bool = True) -> None:
    """Thin separator using muted gray (not orange — orange is reserved)."""
    console = console or Console()
    if pretty:
        console.rule(style=Style(color=TEXT_MUTED))
    else:
        print()
