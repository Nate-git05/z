"""Z color palette — black / white / gray + a single burnt-orange accent."""

# Near-black background (hint for terminals that support it; most CLIs inherit bg)
BACKGROUND = "#0A0A0A"

# Primary readable text
TEXT = "#F5F5F5"
TEXT_DIM = "#A0A0A0"
TEXT_MUTED = "#6B6B6B"

# Single accent — used sparingly for brand, mascot, selection, uncertainty flags
ACCENT = "#C96A2B"
ACCENT_BRIGHT = "#E07830"  # slightly brighter for high-risk / active states
ACCENT_DIM = "#8F4A1F"  # muted orange for lower-priority flags

# Semantic mapping onto Aider's existing color channels
USER_INPUT = TEXT
TOOL_OUTPUT = TEXT_DIM
TOOL_ERROR = TEXT  # stay in palette; no red — use reverse/bold for emphasis
TOOL_WARNING = ACCENT
ASSISTANT_OUTPUT = TEXT
COMPLETION_MENU = TEXT
COMPLETION_MENU_BG = BACKGROUND
COMPLETION_MENU_CURRENT = BACKGROUND
COMPLETION_MENU_CURRENT_BG = ACCENT
CODE_THEME = "monokai"

Z_COLORS = {
    "background": BACKGROUND,
    "text": TEXT,
    "text_dim": TEXT_DIM,
    "text_muted": TEXT_MUTED,
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


def apply_z_palette(args):
    """Apply the Z palette onto an argparse namespace (mutates in place)."""
    args.user_input_color = USER_INPUT
    args.tool_output_color = TOOL_OUTPUT
    args.tool_error_color = TOOL_ERROR
    args.tool_warning_color = TOOL_WARNING
    args.assistant_output_color = ASSISTANT_OUTPUT
    args.completion_menu_color = COMPLETION_MENU
    args.completion_menu_bg_color = COMPLETION_MENU_BG
    args.completion_menu_current_color = COMPLETION_MENU_CURRENT
    args.completion_menu_current_bg_color = COMPLETION_MENU_CURRENT_BG
    args.code_theme = CODE_THEME
    return args
