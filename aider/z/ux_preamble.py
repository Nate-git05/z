"""Quiet turn preamble — P0 terminal UX.

Collects control-plane facts during planning and flushes ≤2 status lines
instead of a wall of skill/explore chatter. Verbose restores the old trail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence


def ux_verbose(*, coder=None, io=None) -> bool:
    """True when the full legacy status trail should print."""
    if os.environ.get("Z_UX_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if coder is not None and getattr(coder, "verbose", False):
        return True
    if io is not None and getattr(io, "verbose", False):
        return True
    return False


def ux_full_plan_first() -> bool:
    """Escape: print full plan before confirm (pre-P0 behavior)."""
    return os.environ.get("Z_UX_FULL_PLAN_FIRST", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@dataclass
class TurnPreamble:
    """Aggregate quiet-turn planning facts; flush ≤2 tool_output lines."""

    verbose: bool = False
    skill_names: List[str] = field(default_factory=list)
    capability_only: bool = False
    capability_gaps: int = 0
    explore_files: int = 0
    plan_gated: bool = False
    plan_approved: Optional[bool] = None
    _flushed: bool = False

    def note_skills(self, names: Sequence[str] | None = None, *, capability_only: bool = False) -> None:
        self.skill_names = [n for n in (names or []) if n]
        self.capability_only = bool(capability_only)

    def note_gaps(self, n: int) -> None:
        self.capability_gaps = max(0, int(n or 0))

    def note_explore(self, n_files: int) -> None:
        self.explore_files = max(0, int(n_files or 0))

    def note_plan(self, *, gated: bool = False, approved: Optional[bool] = None) -> None:
        if gated:
            self.plan_gated = True
        if approved is not None:
            self.plan_approved = bool(approved)

    def format_lines(self) -> List[str]:
        parts: List[str] = []
        if self.skill_names:
            parts.append(f"{len(self.skill_names)} skill" + ("s" if len(self.skill_names) != 1 else ""))
        elif self.capability_only:
            parts.append("capability plan")
        else:
            parts.append("skills —")

        if self.explore_files:
            parts.append(f"explore {self.explore_files} file" + ("s" if self.explore_files != 1 else ""))
        else:
            parts.append("explore —")

        if self.plan_approved is True:
            parts.append("plan approved")
        elif self.plan_gated:
            parts.append("plan-gate")
        else:
            parts.append("plan —")

        line = "Planning · " + " · ".join(parts)
        lines = [line]
        if self.capability_gaps:
            # Gaps stay as a separate warning via tool_warning at the call site;
            # preamble only notes count when flushing status.
            pass
        return lines[:2]

    def flush(self, io) -> None:
        if self._flushed or self.verbose:
            return
        self._flushed = True
        if io is None:
            return
        for line in self.format_lines():
            try:
                io.tool_output(line)
            except Exception:
                pass
