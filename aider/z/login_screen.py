"""Z login screen — branded onboarding UI around the existing auth flows.

Structure: large wordmark + mascot, version/status, bordered "Get started"
box with selectable auth options. Skinned in Z's palette (black / white /
gray + burnt orange).

Presentation only — selection hands off to aider.z.auth.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from rich.console import Console, Group
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from .mascot import idle_mascot_lines
from .theme import ACCENT, ACCENT_DIM, TEXT, TEXT_DIM, TEXT_MUTED

# Solid-block Z only (no box-drawing mix). Mixed █ + ╔╗╚╝ glyphs misalign in
# many terminal fonts and read as a "broken" logo.
LOGIN_WORDMARK = [
    "██████████",
    "       ██ ",
    "      ██  ",
    "     ██   ",
    "    ██    ",
    "   ██     ",
    "  ██      ",
    "██████████",
]

# Extra rows above the brand block reserved for the hop-in animation so Live
# height stays constant (avoids ghost lines / torn frames).
_HOP_MAX = 2

TERMS_URL = "https://github.com/Nate-git05/z"  # placeholder until z.dev terms exist

LOGIN_OPTIONS = [
    ("google", "Continue with Google"),
    ("email", "Continue with Email"),
    ("phone", "Continue with Phone"),
]


@dataclass
class LoginScreenState:
    selected: int = 0
    status_message: str = ""
    version: str = ""


def _pad_line(line: str, width: int) -> str:
    if len(line) >= width:
        return line[:width]
    return line + (" " * (width - len(line)))


def _brand_block(
    *,
    mascot_offset: int = 0,
    unicode_ok: bool | None = None,
    animate_canvas: bool = False,
) -> Text:
    """Wordmark + idle mascot side by side.

    When animate_canvas=True, reserve a fixed hop region above the wordmark so
    Live refresh height does not jump mid-animation. Resting renders omit that
    spacer so the settled screen is flush.
    """
    mark = list(LOGIN_WORDMARK)
    mascot = idle_mascot_lines(unicode_ok)

    mark_w = max(len(line) for line in mark)
    mascot_w = max((len(line) for line in mascot), default=0)
    mark_h = len(mark)
    mascot_h = len(mascot)

    offset = max(0, min(_HOP_MAX, int(mascot_offset)))
    hop_pad = _HOP_MAX if animate_canvas else 0

    total_h = hop_pad + mark_h
    mark_start = hop_pad
    # Resting position: vertically centered on the wordmark; hop lifts it.
    rest = mark_start + max(0, (mark_h - mascot_h) // 2)
    mascot_start = max(0, rest - offset)

    gap = "   "
    accent = Style(color=ACCENT, bold=True)
    out = Text()
    for row in range(total_h):
        if mark_start <= row < mark_start + mark_h:
            left = _pad_line(mark[row - mark_start], mark_w)
        else:
            left = " " * mark_w

        if mascot_start <= row < mascot_start + mascot_h:
            right = _pad_line(mascot[row - mascot_start], mascot_w)
        else:
            right = ""

        out.append(left, style=accent)
        if right:
            out.append(gap, style=accent)
            out.append(right, style=accent)
        out.append("\n")
    return out


def _header_lines(state: LoginScreenState) -> Text:
    out = Text()
    version = state.version or ""
    out.append("Z CLI", style=Style(color=TEXT, bold=True))
    if version:
        out.append(f" {version}", style=Style(color=TEXT_MUTED))
    out.append("\n")
    if state.status_message:
        out.append(state.status_message + "\n", style=Style(color=ACCENT_DIM))
    return out


def _get_started_panel(state: LoginScreenState, *, max_width: int | None = None) -> Panel:
    body = Text()

    body.append("? ", style=Style(color=ACCENT, bold=True))
    body.append("Get started\n\n", style=Style(color=TEXT, bold=True))
    body.append("How would you like to sign in?\n\n", style=Style(color=TEXT))

    for i, (_key, label) in enumerate(LOGIN_OPTIONS):
        row = f" {i + 1}. {label} "
        if i == state.selected:
            # Orange marker + bold orange label — no dark-on-orange bgcolor
            # (bgcolor often fails and leaves near-black text invisible).
            body.append(" ● ", style=Style(color=ACCENT, bold=True))
            body.append(row + "\n", style=Style(color=ACCENT, bold=True))
        else:
            body.append("   ", style=Style(color=TEXT_MUTED))
            body.append(row + "\n", style=Style(color=TEXT_DIM))

    body.append("\n(Use ↑↓ arrows, numbers, or Enter)\n\n", style=Style(color=TEXT_MUTED))
    body.append("Terms of Service and Privacy Notice for Z CLI\n", style=Style(color=TEXT_DIM))
    body.append(TERMS_URL, style=Style(color=ACCENT, underline=True))

    kwargs: dict = {
        "border_style": Style(color=ACCENT_DIM),
        "padding": (0, 2),
        "expand": False,
    }
    if max_width is not None:
        # Keep the box readable without wrapping itself into garbage.
        kwargs["width"] = max(36, min(56, max_width))

    return Panel(body, **kwargs)


def compose_login_screen(
    state: LoginScreenState,
    *,
    mascot_offset: int = 0,
    terminal_width: int | None = None,
    animate_canvas: bool = False,
) -> Group:
    """Full screen renderable: brand block, version/status, get-started box."""
    return Group(
        _brand_block(mascot_offset=mascot_offset, animate_canvas=animate_canvas),
        _header_lines(state),
        _get_started_panel(state, max_width=terminal_width),
    )


def _login_console(console: Console | None = None) -> Console:
    """Console for the login UI — prefer truecolor, never inherit NO_COLOR mute."""
    if console is not None:
        return console
    return Console(force_terminal=True, color_system="auto", soft_wrap=False)


def render_login_screen(
    console: Console | None = None,
    *,
    selected: int = 0,
    version: str = "",
    status_message: str = "",
    mascot_offset: int = 0,
) -> None:
    """One-shot render (used by tests and non-interactive paths)."""
    console = _login_console(console)
    state = LoginScreenState(
        selected=selected, version=version, status_message=status_message
    )
    console.print(
        compose_login_screen(
            state, mascot_offset=mascot_offset, terminal_width=console.width
        )
    )


def _read_key() -> str:
    """Read one keypress (POSIX raw mode). Returns '', 'up', 'down', 'enter', 'esc', or the char."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # escape sequence
            seq = sys.stdin.read(1)
            if seq == "[":
                arrow = sys.stdin.read(1)
                if arrow == "A":
                    return "up"
                if arrow == "B":
                    return "down"
                return ""
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":  # Ctrl-C
            raise KeyboardInterrupt
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _hop_in(console: Console, state: LoginScreenState) -> None:
    """One-time entrance: the mascot hops down into place beside the wordmark."""
    from rich.live import Live

    # Drop from raised → settle on a fixed-height canvas, then hand off to Live.
    frames = [_HOP_MAX, 1, 0, 1, 0]
    with Live(console=console, refresh_per_second=30, transient=True) as live:
        for offset in frames:
            live.update(
                compose_login_screen(
                    state,
                    mascot_offset=offset,
                    terminal_width=console.width,
                    animate_canvas=True,
                )
            )
            time.sleep(0.08)


def interactive_login_select(
    console: Console | None = None,
    *,
    version: str = "",
    status_message: str = "",
    animate: bool = True,
) -> str | None:
    """
    Render the login screen with arrow-key / number selection.

    Returns the chosen provider key ("google" | "email" | "phone") or None.
    Requires a TTY; callers should fall back to prompt_login_choice_plain otherwise.
    """
    from rich.live import Live

    console = _login_console(console)
    state = LoginScreenState(version=version, status_message=status_message)

    if animate:
        try:
            _hop_in(console, state)
        except Exception:
            pass

    try:
        with Live(
            compose_login_screen(state, terminal_width=console.width),
            console=console,
            refresh_per_second=20,
            transient=False,
            vertical_overflow="crop",
        ) as live:
            while True:
                key = _read_key()
                if key == "up":
                    state.selected = (state.selected - 1) % len(LOGIN_OPTIONS)
                elif key == "down":
                    state.selected = (state.selected + 1) % len(LOGIN_OPTIONS)
                elif key in ("1", "2", "3"):
                    state.selected = int(key) - 1
                    live.update(
                        compose_login_screen(state, terminal_width=console.width)
                    )
                    live.refresh()
                    time.sleep(0.08)
                    return LOGIN_OPTIONS[state.selected][0]
                elif key == "enter":
                    return LOGIN_OPTIONS[state.selected][0]
                elif key in ("q", "esc"):
                    return None
                live.update(compose_login_screen(state, terminal_width=console.width))
    except KeyboardInterrupt:
        return None


def prompt_login_choice_plain(io) -> str | None:
    """Non-TTY / non-pretty fallback: numbered menu via the standard prompt."""
    io.tool_output("")
    io.tool_output("Z CLI — sign in")
    for i, (_key, label) in enumerate(LOGIN_OPTIONS):
        io.tool_output(f"  [{i + 1}] {label}")
    io.tool_output("  [q] Cancel")
    io.tool_output("")

    choice = (io.prompt_ask("Choose an option", default="1") or "").strip().lower()
    if choice in ("q", "quit", "cancel", "n", "no", "esc"):
        return None
    mapping = {
        "1": "google",
        "g": "google",
        "google": "google",
        "2": "email",
        "e": "email",
        "email": "email",
        "3": "phone",
        "p": "phone",
        "phone": "phone",
    }
    return mapping.get(choice)


def prompt_login_choice(io, *, version: str = "", status_message: str = "") -> str | None:
    """
    Show the branded login screen and return the chosen provider key.

    Uses the interactive arrow-key screen on a real terminal; otherwise a
    plain numbered menu through the standard IO prompt.
    """
    pretty = bool(getattr(io, "pretty", False))
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()

    if pretty and is_tty:
        # Prefer a dedicated console so login colors are not muted by a
        # no_color console left over from earlier init edge cases.
        console = Console(force_terminal=True, color_system="auto", soft_wrap=False)
        try:
            return interactive_login_select(
                console, version=version, status_message=status_message
            )
        except Exception:
            return prompt_login_choice_plain(io)
    return prompt_login_choice_plain(io)
