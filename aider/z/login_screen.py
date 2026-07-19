"""Z login screen — branded onboarding UI around the existing auth flows.

Uses plain ASCII for the wordmark/box so macOS Terminal (and other fonts that
render █ / box-drawing as double-width) cannot shred the layout. Selection is
redrawn with a full-screen clear — not Rich Live — for the same reason.

Presentation only — selection hands off to aider.z.auth.
"""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass

from rich.console import Console
from rich.style import Style
from rich.text import Text

from .mascot import idle_mascot_lines
from .theme import ACCENT, ACCENT_DIM, TEXT, TEXT_DIM, TEXT_MUTED

# Pure ASCII Z — single cell width everywhere (avoids Mac █ double-width wrap).
LOGIN_WORDMARK = [
    "##########",
    "       ## ",
    "      ##  ",
    "     ##   ",
    "    ##    ",
    "   ##     ",
    "  ##      ",
    "##########",
]

TERMS_URL = "https://github.com/Nate-git05/z"

LOGIN_OPTIONS = [
    ("google", "Continue with Google"),
    ("email", "Continue with Email"),
    ("phone", "Continue with Phone"),
]

AUTH_MODE_OPTIONS = [
    ("byok", "Bring your own API key"),
    ("router", "Sign up / sign in (use Z's router)"),
]

OptionList = list[tuple[str, str]]


@dataclass
class LoginScreenState:
    selected: int = 0
    status_message: str = ""
    version: str = ""


def _pad_line(line: str, width: int) -> str:
    if len(line) >= width:
        return line[:width]
    return line + (" " * (width - len(line)))


def _brand_lines(*, unicode_ok: bool | None = None) -> list[str]:
    del unicode_ok  # login always uses ASCII mascot for layout stability
    mark = list(LOGIN_WORDMARK)
    mascot = idle_mascot_lines(False)

    mark_w = max(len(line) for line in mark)
    mascot_w = max((len(line) for line in mascot), default=0)
    mark_h = len(mark)
    mascot_h = len(mascot)
    top = max(0, (mark_h - mascot_h) // 2)

    lines: list[str] = []
    for i in range(mark_h):
        left = _pad_line(mark[i], mark_w)
        mi = i - top
        right = _pad_line(mascot[mi], mascot_w) if 0 <= mi < mascot_h else ""
        if right.strip():
            lines.append(f"{left}   {right.rstrip()}")
        else:
            lines.append(left.rstrip())
    return lines


def _menu_inner_lines(
    state: LoginScreenState,
    *,
    options: OptionList | None = None,
    prompt_text: str = "How would you like to sign in?",
) -> list[tuple[str, str]]:
    """Return (plain_line, style_role) rows for the get-started box body."""
    opts = options if options is not None else LOGIN_OPTIONS
    rows: list[tuple[str, str]] = [
        ("? Get started", "title"),
        ("", "blank"),
        (prompt_text, "text"),
        ("", "blank"),
    ]
    for i, (_key, label) in enumerate(opts):
        if i == state.selected:
            rows.append((f" > {i + 1}. {label}", "selected"))
        else:
            rows.append((f"   {i + 1}. {label}", "option"))
    rows.extend(
        [
            ("", "blank"),
            ("(Use up/down arrows, numbers, or Enter)", "muted"),
            ("", "blank"),
            ("Terms of Service and Privacy Notice for Z CLI", "dim"),
            (TERMS_URL, "link"),
        ]
    )
    return rows


def _ascii_box(inner: list[tuple[str, str]], *, inner_width: int) -> Text:
    """Draw a simple ASCII box — no unicode rounded borders."""
    out = Text()
    top = "+" + ("-" * (inner_width + 2)) + "+"
    out.append(top + "\n", style=Style(color=ACCENT_DIM))

    role_styles = {
        "title": Style(color=TEXT, bold=True),
        "text": Style(color=TEXT),
        "selected": Style(color=ACCENT, bold=True),
        "option": Style(color=TEXT_DIM),
        "muted": Style(color=TEXT_MUTED),
        "dim": Style(color=TEXT_DIM),
        "link": Style(color=ACCENT, underline=True),
        "blank": Style(color=TEXT_MUTED),
    }

    for plain, role in inner:
        # Highlight selected row marker in accent; keep '?' accent on title.
        content = Text()
        if role == "title" and plain.startswith("? "):
            content.append("? ", style=Style(color=ACCENT, bold=True))
            content.append(plain[2:], style=role_styles["title"])
        else:
            content.append(plain, style=role_styles.get(role, Style(color=TEXT)))

        # Pad plain width for the border; Rich Text display width ~= len(plain) for ASCII.
        pad = max(0, inner_width - len(plain))
        out.append("| ", style=Style(color=ACCENT_DIM))
        out.append(content)
        if pad:
            out.append(" " * pad)
        out.append(" |\n", style=Style(color=ACCENT_DIM))

    out.append("+" + ("-" * (inner_width + 2)) + "+", style=Style(color=ACCENT_DIM))
    return out


def compose_login_text(
    state: LoginScreenState,
    *,
    terminal_width: int = 80,
    options: OptionList | None = None,
    prompt_text: str = "How would you like to sign in?",
) -> Text:
    """Build the full login screen as a single Text (no Live/Panel)."""
    out = Text()
    accent = Style(color=ACCENT, bold=True)

    for line in _brand_lines():
        out.append(line + "\n", style=accent)

    out.append("\n")
    out.append("Z CLI", style=Style(color=TEXT, bold=True))
    if state.version:
        out.append(f" {state.version}", style=Style(color=TEXT_MUTED))
    out.append("\n")
    if state.status_message:
        out.append(state.status_message + "\n", style=Style(color=ACCENT_DIM))
    out.append("\n")

    inner = _menu_inner_lines(state, options=options, prompt_text=prompt_text)
    content_w = max((len(p) for p, _ in inner), default=40)
    # Fit terminal: borders take 4 cols ("| " + " |")
    max_inner = max(32, min(52, terminal_width - 4))
    inner_width = max(content_w, min(max_inner, max(content_w, 40)))
    # If terminal is very narrow, shrink and let long lines truncate in pad math
    if terminal_width < inner_width + 4:
        inner_width = max(28, terminal_width - 4)

    # Truncate overlong plain lines so the box never wraps.
    trimmed: list[tuple[str, str]] = []
    for plain, role in inner:
        if len(plain) > inner_width:
            trimmed.append((plain[: max(0, inner_width - 3)] + "...", role))
        else:
            trimmed.append((plain, role))

    out.append(_ascii_box(trimmed, inner_width=inner_width))
    out.append("\n")
    return out


# Back-compat name used by tests
def compose_login_screen(state: LoginScreenState, **kwargs):
    width = kwargs.get("terminal_width") or shutil.get_terminal_size((80, 24)).columns
    return compose_login_text(state, terminal_width=width)


def _login_console(console: Console | None = None) -> Console:
    if console is not None:
        return console
    return Console(force_terminal=True, color_system="auto", soft_wrap=False, width=None)


def render_login_screen(
    console: Console | None = None,
    *,
    selected: int = 0,
    version: str = "",
    status_message: str = "",
    mascot_offset: int = 0,  # kept for API compat; unused (no hop)
) -> None:
    del mascot_offset  # hop animation removed — it tore layouts on macOS
    console = _login_console(console)
    state = LoginScreenState(
        selected=selected, version=version, status_message=status_message
    )
    console.print(compose_login_text(state, terminal_width=console.width or 80))


def _read_key() -> str:
    """Read one keypress (POSIX raw mode)."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
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
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _clear_screen() -> None:
    # Clear + home. Avoid Rich Live, which compounds width mis-measure into debris.
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def interactive_login_select(
    console: Console | None = None,
    *,
    version: str = "",
    status_message: str = "",
    animate: bool = True,
    options: OptionList | None = None,
    prompt_text: str = "How would you like to sign in?",
) -> str | None:
    """
    Render the login screen with arrow-key / number selection.

    Returns the chosen option key, or None if cancelled.
    """
    del animate  # no hop — static redraw only
    opts = options if options is not None else LOGIN_OPTIONS
    if not opts:
        return None
    console = _login_console(console)
    state = LoginScreenState(version=version, status_message=status_message)
    number_keys = {str(i + 1) for i in range(len(opts))}

    try:
        while True:
            _clear_screen()
            console.print(
                compose_login_text(
                    state,
                    terminal_width=console.width or 80,
                    options=opts,
                    prompt_text=prompt_text,
                )
            )
            key = _read_key()
            if key == "up":
                state.selected = (state.selected - 1) % len(opts)
            elif key == "down":
                state.selected = (state.selected + 1) % len(opts)
            elif key in number_keys:
                state.selected = int(key) - 1
                _clear_screen()
                console.print(
                    compose_login_text(
                        state,
                        terminal_width=console.width or 80,
                        options=opts,
                        prompt_text=prompt_text,
                    )
                )
                time.sleep(0.05)
                return opts[state.selected][0]
            elif key == "enter":
                return opts[state.selected][0]
            elif key in ("q", "esc"):
                return None
    except KeyboardInterrupt:
        return None


def _plain_choice_menu(
    io,
    *,
    title: str,
    options: OptionList,
) -> str | None:
    """Numbered menu via the standard prompt (non-TTY / non-pretty)."""
    io.tool_output("")
    io.tool_output(title)
    for i, (_key, label) in enumerate(options):
        io.tool_output(f"  [{i + 1}] {label}")
    io.tool_output("  [q] Cancel")
    io.tool_output("")

    choice = (io.prompt_ask("Choose an option", default="1") or "").strip().lower()
    if choice in ("q", "quit", "cancel", "n", "no", "esc"):
        return None
    mapping: dict[str, str] = {}
    for i, (key, _label) in enumerate(options):
        mapping[str(i + 1)] = key
        mapping[key.lower()] = key
        # Single-letter shortcut from the option key when unambiguous
        if key and key[0].lower() not in mapping:
            mapping[key[0].lower()] = key
    return mapping.get(choice)


def prompt_login_choice_plain(io) -> str | None:
    """Non-TTY / non-pretty fallback: numbered menu via the standard prompt."""
    return _plain_choice_menu(io, title="Z CLI — sign in", options=LOGIN_OPTIONS)


def prompt_login_choice(io, *, version: str = "", status_message: str = "") -> str | None:
    """Show the branded login screen and return the chosen provider key."""
    pretty = bool(getattr(io, "pretty", False))
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()

    if pretty and is_tty:
        console = Console(force_terminal=True, color_system="auto", soft_wrap=False)
        try:
            return interactive_login_select(
                console, version=version, status_message=status_message
            )
        except Exception:
            return prompt_login_choice_plain(io)
    return prompt_login_choice_plain(io)


def prompt_auth_mode_choice_plain(io) -> str | None:
    return _plain_choice_menu(
        io,
        title="How would you like to use Z?",
        options=AUTH_MODE_OPTIONS,
    )


def prompt_auth_mode_choice(
    io, *, version: str = "", status_message: str = ""
) -> str | None:
    """Ask BYOK vs Z router — same UI primitives as the login screen."""
    pretty = bool(getattr(io, "pretty", False))
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()

    if pretty and is_tty:
        console = Console(force_terminal=True, color_system="auto", soft_wrap=False)
        try:
            return interactive_login_select(
                console,
                version=version,
                status_message=status_message,
                options=AUTH_MODE_OPTIONS,
                prompt_text="How would you like to use Z?",
            )
        except Exception:
            return prompt_auth_mode_choice_plain(io)
    return prompt_auth_mode_choice_plain(io)
