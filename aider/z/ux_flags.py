"""P1 terminal UX flags — usage opt-in and history mirroring."""

from __future__ import annotations

import os
from typing import Any, Optional


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def show_usage_enabled(*, coder: Any = None, io: Any = None) -> bool:
    """True when Tokens/Cost lines should print after an LLM round."""
    if env_truthy("Z_SHOW_USAGE"):
        return True
    if coder is not None and getattr(coder, "show_cost", False):
        return True
    if io is not None and getattr(io, "show_cost", False):
        return True
    if coder is not None:
        cio = getattr(coder, "io", None)
        if cio is not None and getattr(cio, "show_cost", False):
            return True
    return False


def history_mirror_status_enabled() -> bool:
    """True when T1 ``tool_output`` lines should be blockquoted into chat history."""
    return env_truthy("Z_UX_HISTORY_FULL")
