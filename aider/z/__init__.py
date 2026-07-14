"""Z — account auth, MCP, skills, uncertainty tree, and terminal UI branding."""

from .auth import (
    current_session,
    logout,
    require_account,
    run_login_flow,
    whoami_text,
)
from .banner import render_startup_banner
from .credentials import Credentials, apply_credentials_to_env, load_credentials, save_credentials
from .escalation import render_escalation
from .mascot import MascotSpinner, idle_mascot_lines, working_mascot_frame
from .mcp_client import fetch_mcp_runtime, get_session_mcp_tools, load_mcp_tools_for_session
from .paths import CREDENTIALS_PATH, Z_HOME
from .theme import Z_COLORS, apply_z_palette

__all__ = [
    "Z_HOME",
    "CREDENTIALS_PATH",
    "Credentials",
    "apply_credentials_to_env",
    "load_credentials",
    "save_credentials",
    "current_session",
    "run_login_flow",
    "require_account",
    "logout",
    "whoami_text",
    "fetch_mcp_runtime",
    "get_session_mcp_tools",
    "load_mcp_tools_for_session",
    "Z_COLORS",
    "apply_z_palette",
    "MascotSpinner",
    "idle_mascot_lines",
    "working_mascot_frame",
    "render_startup_banner",
    "render_escalation",
]
