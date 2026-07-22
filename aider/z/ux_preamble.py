"""Quiet turn preamble — P0 / quiet-turn terminal UX.

Collects control-plane facts during planning. By default the busy spinner
shows a short phase-kind label (Thinking…/Exploring…/Planning…/Waiting for
model…) that changes live as the turn progresses, with retained "✓ …" lines
left behind on each transition (see aider/z/phase_kinds.py). Verbose /
Z_UX_VERBOSE restores the old, fully detailed status trail; Z_UX_PREAMBLE
restores the compact Planning line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

DEFAULT_BUSY_LABEL = "Working…"
DEFAULT_WAITING_MODEL_LABEL = "Waiting for model…"


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


def ux_preamble_enabled() -> bool:
    """Escape: restore the compact one-line Planning · … preamble flush."""
    return os.environ.get("Z_UX_PREAMBLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def confirm_new_files_enabled() -> bool:
    """Escape: ask before creating each new file (legacy draggy path)."""
    return os.environ.get("Z_CONFIRM_NEW_FILES", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def public_busy_label(
    detailed: Optional[str] = None,
    *,
    coder=None,
    io=None,
    waiting_model: bool = False,
) -> str:
    """User-facing busy spinner text.

    Verbose (Z_UX_VERBOSE / coder.verbose) shows the full detailed phase
    string unabridged. Non-verbose (the default) shows a short phase-kind
    label inferred from ``detailed`` — visible by default, not silent — so
    the live indicator still reads as Thinking/Exploring/Planning/etc.
    rather than one generic "Working…" the whole turn.

    ``waiting_model`` is now only load-bearing as a defensive fallback when
    ``detailed`` is empty — kind inference already detects a "Waiting for "
    prefix in the text itself.
    """
    if ux_verbose(coder=coder, io=io):
        text = (detailed or "").strip()
        if text:
            return text
        return DEFAULT_WAITING_MODEL_LABEL if waiting_model else DEFAULT_BUSY_LABEL
    from .phase_kinds import LIVE_LABELS, infer_phase_kind

    text = (detailed or "").strip()
    if not text:
        return DEFAULT_WAITING_MODEL_LABEL if waiting_model else DEFAULT_BUSY_LABEL
    return LIVE_LABELS.get(infer_phase_kind(text), DEFAULT_BUSY_LABEL)


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

    def has_substance(self) -> bool:
        """True when a compact preamble would say something other than dashes."""
        return bool(
            self.skill_names
            or self.capability_only
            or self.explore_files
            or self.plan_gated
            or self.plan_approved is not None
            or self.capability_gaps
        )

    def format_lines(self) -> List[str]:
        # Never emit the empty "Planning · skills — · explore — · plan —" line.
        if not self.has_substance():
            return []
        parts: List[str] = []
        if self.skill_names:
            parts.append(f"{len(self.skill_names)} skill" + ("s" if len(self.skill_names) != 1 else ""))
        elif self.capability_only:
            parts.append("capability plan")

        if self.explore_files:
            parts.append(f"explore {self.explore_files} file" + ("s" if self.explore_files != 1 else ""))

        if self.plan_approved is True:
            parts.append("plan approved")
        elif self.plan_gated:
            parts.append("plan-gate")

        if not parts:
            return []
        return [("Planning · " + " · ".join(parts))][:2]

    def flush(self, io) -> None:
        if self._flushed or self.verbose:
            return
        self._flushed = True
        # Quiet by default: skills/explore/plan are not narrated.
        # Opt back in with Z_UX_PREAMBLE=1 for a compact line *with substance*.
        if not ux_preamble_enabled():
            return
        if io is None:
            return
        for line in self.format_lines():
            try:
                io.tool_output(line)
            except Exception:
                pass
