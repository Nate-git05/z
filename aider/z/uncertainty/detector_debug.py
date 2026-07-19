"""Shared Z_DETECTOR_DEBUG gate for cheap-detector instrumentation.

Print-only — never changes control flow. Used from base_coder.py and
detectors.py so both emit the same ``[detector-debug]`` prefix.
"""

from __future__ import annotations

import os
from typing import Any, Optional


def detector_debug_enabled() -> bool:
    return os.environ.get("Z_DETECTOR_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def detector_debug(msg: str, *, io: Any = None) -> None:
    """Emit one ``[detector-debug]`` line when the env flag is on."""
    if not detector_debug_enabled():
        return
    line = f"[detector-debug] {msg}"
    if io is not None and hasattr(io, "tool_output"):
        try:
            io.tool_output(line)
            return
        except Exception:
            pass
    try:
        print(line, flush=True)
    except Exception:
        pass
