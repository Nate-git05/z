"""Z color palette — warm soft-black + sherbet orange accent.

Semantic tiers (P0 terminal UX / UI redesign):
  - Status / routine tool output → warm secondary (readable, not a warning)
  - Warnings + escalation borders → sherbet / dim sherbet
  - Spinner / brand progress labels → sherbet (TEXT_DIM)
  - Comfortable warm blacks; no purple in code highlighting
"""

from __future__ import annotations

import os

# Warm soft-black (never pure #000)
BACKGROUND = "#0E0D0C"

# Primary readable text — warm off-white (never pure #FFF)
TEXT = "#F2EDE7"
# Branded progress (mascot spinner labels) — sherbet
TEXT_DIM = "#F7A56B"
# Secondary / panel subtitle
TEXT_MUTED = "#9C948A"

# Single accent — brand, warnings, escalation borders, uncertainty flags
ACCENT = "#F7A56B"
ACCENT_BRIGHT = "#F7A56B"
ACCENT_DIM = "#C98858"

# Status channel — routine tool_output (not a warning)
STATUS = "#9C948A"


def hex_to_ansi_fg(hex_color: str) -> str:
    """Convert #RRGGBB to an ANSI 24-bit foreground escape.

    Single source of truth for plain/non-pretty renderers so they match
    theme.py's actual palette instead of hand-picked raw ANSI constants
    that can silently drift from it.
    """
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"\033[38;2;{r};{g};{b}m"


ANSI_RESET = "\033[0m"


def _status_color() -> str:
    raw = (os.environ.get("Z_STATUS_COLOR") or "").strip()
    if len(raw) == 7 and raw.startswith("#"):
        return raw.upper()
    return STATUS


# Semantic mapping onto Aider's existing color channels
USER_INPUT = TEXT
TOOL_OUTPUT = STATUS  # routine status — distinct from warnings
TOOL_ERROR = TEXT  # stay in palette; no red — use reverse/bold for emphasis
TOOL_WARNING = ACCENT
ASSISTANT_OUTPUT = TEXT  # white — never purple/blue
COMPLETION_MENU = TEXT
COMPLETION_MENU_BG = BACKGROUND
COMPLETION_MENU_CURRENT = BACKGROUND
COMPLETION_MENU_CURRENT_BG = ACCENT
CODE_THEME = "z-terminal"

Z_COLORS = {
    "background": BACKGROUND,
    "text": TEXT,
    "text_dim": TEXT_DIM,
    "text_muted": TEXT_MUTED,
    "status": STATUS,
    "accent": ACCENT,
    "accent_bright": ACCENT_BRIGHT,
    "accent_dim": ACCENT_DIM,
    "user_input": USER_INPUT,
    "tool_output": TOOL_OUTPUT,
    "tool_error": TOOL_ERROR,
    "tool_warning": TOOL_WARNING,
    "assistant_output": ASSISTANT_OUTPUT,
    "completion_menu": COMPLETION_MENU,
    "completion_menu_bg": COMPLETION_MENU_BG,
    "completion_menu_current": COMPLETION_MENU_CURRENT,
    "completion_menu_current_bg": COMPLETION_MENU_CURRENT_BG,
    "code_theme": CODE_THEME,
}


try:
    from pygments.style import Style
    from pygments.token import (
        Comment,
        Error,
        Generic,
        Keyword,
        Name,
        Number,
        Operator,
        Punctuation,
        String,
        Text as TokText,
        Token,
    )

    class ZTerminalStyle(Style):
        """White text + orange accents only — no grey, no purple."""

        name = "z-terminal"
        background_color = BACKGROUND
        highlight_color = "#1E1B18"
        styles = {
            Token: TEXT,
            TokText: TEXT,
            Comment: ACCENT,
            Comment.Hashbang: ACCENT,
            Comment.Multiline: ACCENT,
            Comment.Preproc: ACCENT,
            Comment.Single: ACCENT,
            Comment.Special: ACCENT_BRIGHT,
            Keyword: TEXT,
            Keyword.Constant: TEXT,
            Keyword.Declaration: TEXT,
            Keyword.Namespace: TEXT,
            Keyword.Pseudo: ACCENT,
            Keyword.Reserved: TEXT,
            Keyword.Type: TEXT,
            Name: TEXT,
            Name.Attribute: ACCENT_BRIGHT,
            Name.Builtin: TEXT,
            Name.Builtin.Pseudo: TEXT,
            Name.Class: TEXT,
            Name.Constant: TEXT,  # monokai used purple here
            Name.Decorator: ACCENT,
            Name.Entity: TEXT,
            Name.Exception: TEXT,
            Name.Function: TEXT,
            Name.Label: TEXT,
            Name.Namespace: TEXT,
            Name.Other: TEXT,
            Name.Property: TEXT,
            Name.Tag: ACCENT,
            Name.Variable: TEXT,
            Number: ACCENT_BRIGHT,
            Operator: TEXT,
            Operator.Word: TEXT,
            Punctuation: TEXT,
            String: ACCENT_BRIGHT,
            String.Affix: ACCENT_BRIGHT,
            String.Backtick: ACCENT_BRIGHT,
            String.Char: ACCENT_BRIGHT,
            String.Delimiter: ACCENT,
            String.Doc: ACCENT,
            String.Double: ACCENT_BRIGHT,
            String.Escape: ACCENT,
            String.Heredoc: ACCENT_BRIGHT,
            String.Interpol: ACCENT_BRIGHT,
            String.Other: ACCENT_BRIGHT,
            String.Regex: ACCENT_BRIGHT,
            String.Single: ACCENT_BRIGHT,
            String.Symbol: ACCENT_BRIGHT,
            Generic.Deleted: TEXT,
            Generic.Emph: f"italic {TEXT}",
            Generic.Error: TEXT,
            Generic.Heading: f"bold {TEXT}",
            Generic.Inserted: TEXT,
            Generic.Output: ACCENT,
            Generic.Prompt: ACCENT,
            Generic.Strong: f"bold {TEXT}",
            Generic.Subheading: f"bold {ACCENT}",
            Generic.Traceback: TEXT,
            Error: TEXT,
        }

except ImportError:  # pragma: no cover - pygments always present in Z installs

    class ZTerminalStyle:  # type: ignore[no-redef]
        name = "z-terminal"


def _register_z_terminal_style() -> None:
    """Register the white+orange pygments style as ``z-terminal``."""
    try:
        from pygments.styles import STYLE_MAP, _STYLE_NAME_TO_MODULE_MAP

        # Pygments 2.14+ resolves via _STYLE_NAME_TO_MODULE_MAP, not STYLE_MAP.
        _STYLE_NAME_TO_MODULE_MAP["z-terminal"] = ("aider.z.theme", "ZTerminalStyle")
        STYLE_MAP["z-terminal"] = "theme::ZTerminalStyle"
    except Exception:
        pass


_register_z_terminal_style()


def apply_z_palette(args):
    """Apply the Z palette onto an argparse namespace (mutates in place)."""
    _register_z_terminal_style()
    status = _status_color()
    args.user_input_color = USER_INPUT
    args.tool_output_color = status
    args.tool_error_color = TOOL_ERROR
    args.tool_warning_color = TOOL_WARNING
    args.assistant_output_color = ASSISTANT_OUTPUT
    args.completion_menu_color = COMPLETION_MENU
    args.completion_menu_bg_color = COMPLETION_MENU_BG
    args.completion_menu_current_color = COMPLETION_MENU_CURRENT
    args.completion_menu_current_bg_color = COMPLETION_MENU_CURRENT_BG
    args.code_theme = CODE_THEME
    return args
