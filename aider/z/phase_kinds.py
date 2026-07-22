"""Terminal-native phase-kind taxonomy for the live turn indicator.

Collapses the ~10 ad hoc phase strings scattered through
``aider/coders/base_coder.py`` (e.g. "Planning — exploring related files…")
into a small vocabulary so the spinner can show a short, meaningful label by
default and leave a retained "done" line behind on each transition — the
terminal-native equivalent of an IDE's live step list.

Deliberately separate from ``aider/z/app_server/activity.py``'s own phase
bucketing for the VS Code desktop extension's JSON-RPC ``TurnStep`` system.
That module folds phases into a different vocabulary for a different
consumer (a webview, not a terminal). Do not import between the two.
"""

from __future__ import annotations

from typing import Dict, Optional

THINKING = "thinking"
EXPLORING = "exploring"
PLANNING = "planning"
EDITING = "editing"
VERIFYING = "verifying"
WAITING_MODEL = "waiting_model"

ALL_KINDS = (THINKING, EXPLORING, PLANNING, EDITING, VERIFYING, WAITING_MODEL)

# Short, present-tense text shown by the live spinner (visible by default).
LIVE_LABELS: Dict[str, str] = {
    THINKING: "Thinking…",
    EXPLORING: "Exploring…",
    PLANNING: "Planning…",
    EDITING: "Editing…",
    VERIFYING: "Verifying…",
    WAITING_MODEL: "Waiting for model…",
}

# Scrollback line printed once a phase kind finishes (None = no retained
# line — the model's own streamed reply is itself the completion signal for
# WAITING_MODEL, so a "done" line there would be redundant noise).
RETAINED_LABELS: Dict[str, Optional[str]] = {
    THINKING: "✓ Thought it through",
    EXPLORING: "✓ Explored the codebase",
    PLANNING: "✓ Planned the approach",
    EDITING: "✓ Edited the files",
    VERIFYING: "✓ Verified the changes",
    WAITING_MODEL: None,
}

DEFAULT_KIND = THINKING


def infer_phase_kind(text: Optional[str]) -> str:
    """Map a raw phase string to a small kind vocabulary.

    Ordered, most-specific-first substring rules — every real call site's
    text starts with "Planning — ", so that catch-all must stay last.
    """
    t = (text or "").strip().lower()
    if not t:
        return DEFAULT_KIND
    if "waiting for" in t:
        return WAITING_MODEL
    if "explor" in t:
        return EXPLORING
    if "matching skill" in t or "routing skill" in t or "plan interview" in t:
        return THINKING
    if "editing" in t:
        return EDITING
    if "verifying" in t:
        return VERIFYING
    if "planning" in t:
        return PLANNING
    return DEFAULT_KIND
