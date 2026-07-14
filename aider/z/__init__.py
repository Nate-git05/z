"""Z terminal UI — branding, mascot, uncertainty views, and escalation prompts."""

from .banner import render_startup_banner
from .escalation import render_escalation
from .mascot import MascotSpinner, idle_mascot_lines, working_mascot_frame
from .theme import Z_COLORS, apply_z_palette
from .uncertainty import (
    UncertaintyNote,
    UncertaintyStore,
    UncertaintyTier,
    render_note_detail,
    render_uncertainty_tree,
)

__all__ = [
    "Z_COLORS",
    "apply_z_palette",
    "MascotSpinner",
    "idle_mascot_lines",
    "working_mascot_frame",
    "render_startup_banner",
    "UncertaintyNote",
    "UncertaintyStore",
    "UncertaintyTier",
    "render_note_detail",
    "render_uncertainty_tree",
    "render_escalation",
]
