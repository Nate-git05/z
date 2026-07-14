"""Z login screen — branded onboarding UI around the existing auth flows.

Structure mirrors familiar CLI onboarding (large wordmark, version line,
status line, bordered "Get started" box with selectable auth options),
skinned entirely in Z's palette: black / white / gray + burnt orange.

This module is presentation only. Selection hands off to the auth flows in
aider.z.auth — it never implements auth itself.
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
from .theme import ACCENT, ACCENT_DIM, BACKGROUND, TEXT, TEXT_DIM, TEXT_MUTED

# Large pixel-block Z wordmark — bolder than the in-session banner mark.
LOGIN_WORDMARK = [
    r"██████████╗",
    r"╚═════███╔╝",
    r"     ███╔╝ ",
    r"    ███╔╝  ",
    r"   ███╔╝   ",
    r"  ███╔╝    ",
    r"██████████╗",
    r"╚═════════╝",
]

TERMS_URL = "https://github.com/Nate-git05/z"  # placeholder until z.dev terms exist

# Auth options — Z's actual methods (order per design spec)
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


def _brand_block(*, mascot_offset: int = 0, unicode_ok: bool | None = None) -> Text:
    """Wordmark + idle mascot side by side; mascot_offset lifts the mascot for the hop-in."""
    mark = list(LOGIN_WORDMARK)
    mascot = idle_mascot_lines(unicode_ok)

    mark_h = len(mark)
    mascot_h = len(mascot)
    base_top = max(0, (mark_h - mascot_h) // 2)
    top = max(0, base_top - mascot_offset)
    padded = ([""] * top) + mascot
    padded += [""] * max(0, mark_h - len(padded))

    out = Text()
    accent = Style(color=ACCENT, bold=True)
    for i, mark_line in enumerate(mark):
        out.append(mark_line, style=accent)
        out.append("   ")
        out.append(padded[i] if i < len(padded) else "", style=accent)
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


def _get_started_panel(state: LoginScreenState) -> Panel:
    body = Text()

    body.append("? ", style=Style(color=ACCENT, bold=True))
    body.append("Get started\n\n", style=Style(color=TEXT, bold=True))

    body.append("How would you like to sign in?\n\n", style=Style(color=TEXT))

    for i, (_key, label) in enumerate(LOGIN_OPTIONS):
        row = f" {i + 1}. {label} "
        if i == state.selected:
            body.append(" ● ", style=Style(color=ACCENT, bold=True))
            body.append(row + "\n", style=Style(color=BACKGROUND, bgcolor=ACCENT, bold=True))
        else:
            body.append("   ", style=Style(color=TEXT_MUTED))
            body.append(row + "\n", style=Style(color=TEXT_DIM))

    body.append("\n(Use Enter to select)\n\n", style=Style(color=TEXT_MUTED))

    body.append("Terms of Service and Privacy Notice for Z CLI\n", style=Style(color=TEXT_DIM))
    body.append(TERMS_URL, style=Style(color=ACCENT, underline=True))

    return Panel(
        body,
        border_style=Style(color=ACCENT_DIM),
        padding=(0, 2),
        expand=False,
    )


def compose_login_screen(state: LoginScreenState, *, mascot_offset: int = 0) -> Group:
    """Full screen renderable: brand block, version/status, get-started box."""
    return Group(
        _brand_block(mascot_offset=mascot_offset),
        _header_lines(state),
        _get_started_panel(state),
    )


def render_login_screen(
    console: Console | None = None,
    *,
    selected: int = 0,
    version: str = "",
    status_message: str = "",
    mascot_offset: int = 0,
) -> None:
    """One-shot render (used by tests and non-interactive paths)."""
    console = console or Console()
    state = LoginScreenState(
        selected=selected, version=version, status_message=status_message
    )
    console.print(compose_login_screen(state, mascot_offset=mascot_offset))


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

    frames = [3, 1, 0, 1, 0]  # drop, settle bounce
    with Live(console=console, refresh_per_second=30, transient=True) as live:
        for offset in frames:
            live.update(compose_login_screen(state, mascot_offset=offset))
            time.sleep(0.09)


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

    console = console or Console()
    state = LoginScreenState(version=version, status_message=status_message)

    if animate:
        try:
            _hop_in(console, state)
        except Exception:
            pass

    try:
        with Live(
            compose_login_screen(state),
            console=console,
            refresh_per_second=30,
            transient=False,
        ) as live:
            while True:
                key = _read_key()
                if key == "up":
                    state.selected = (state.selected - 1) % len(LOGIN_OPTIONS)
                elif key == "down":
                    state.selected = (state.selected + 1) % len(LOGIN_OPTIONS)
                elif key in ("1", "2", "3"):
                    state.selected = int(key) - 1
                    live.update(compose_login_screen(state))
                    live.refresh()
                    time.sleep(0.08)
                    return LOGIN_OPTIONS[state.selected][0]
                elif key == "enter":
                    return LOGIN_OPTIONS[state.selected][0]
                elif key in ("q", "esc"):
                    return None
                live.update(compose_login_screen(state))
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
        console = getattr(io, "console", None) or Console()
        try:
            return interactive_login_select(
                console, version=version, status_message=status_message
            )
        except Exception:
            # Any terminal weirdness → plain fallback, never block login
            return prompt_login_choice_plain(io)
    return prompt_login_choice_plain(io)
