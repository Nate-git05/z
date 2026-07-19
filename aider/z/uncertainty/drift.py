"""Drift / fixation detection during the reflection loop.

Flags when consecutive reflections make only off-checklist-scope edits while
unresolved checklist items stay stuck — then offers a confirm-gated refocus
(or records a Medium node if declined). Presentation only: nothing is reverted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .checklist import (
    extract_investigation_targets,
    extract_product_file_paths,
    _normalize_repo_path,
    _path_matches_required,
)
from .evidence_strategy import (
    STATUS_FULLY,
    STATUS_NOT,
    STATUS_PARTIAL,
    STATUS_RANK,
    STATUS_UNVERIFIABLE,
)
from .schema import (
    NodeStatus,
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
    UncertaintyNode,
)

# Statuses that still need work — progress means leaving this set upward.
_OPEN_STATUSES = frozenset({STATUS_NOT, STATUS_PARTIAL, STATUS_UNVERIFIABLE})


@dataclass
class ReflectionTurn:
    """One completed reflection send_message (not the original user turn)."""

    files: Set[str] = field(default_factory=set)
    progressed: bool = False
    off_scope: List[str] = field(default_factory=list)


@dataclass
class DriftSignal:
    off_scope_files: List[str]
    unresolved: List[RequirementItem]
    summary: str


def _usable_scope_symbol(tok: str) -> bool:
    """Reject sentence-starter leftovers (``Make``) that look 'strong' via PascalCase."""
    if not tok:
        return False
    if "/" in tok or "::" in tok:
        return True
    leaf = tok.split("::")[-1].split("/")[-1]
    if "." in leaf:
        return True
    if "_" in leaf and len(leaf) >= 4:
        return True
    # Real camelCase/PascalCase identifiers are usually longer than one English word
    if len(leaf) >= 6 and re.search(r"[a-z]", leaf) and re.search(r"[A-Z]", leaf):
        return True
    return False


def checklist_scope(
    checklist: TaskChecklist,
) -> Tuple[List[str], List[str]]:
    """Named file paths + usable investigation symbols across checklist items.

    Bare weak tokens from ``extract_investigation_targets`` (e.g. sentence
    starters) are ignored so a vague item does not invent fake scope.
    """
    paths: List[str] = []
    symbols: List[str] = []
    for item in checklist.items or []:
        for p in extract_product_file_paths(item.text or ""):
            if p not in paths:
                paths.append(p)
        for t in extract_investigation_targets(item.text or ""):
            if not _usable_scope_symbol(t):
                continue
            if t not in symbols:
                symbols.append(t)
    return paths, symbols


def file_in_checklist_scope(
    path: str,
    path_needles: Sequence[str],
    symbol_needles: Sequence[str],
) -> bool:
    """True when *path* matches a checklist file or investigation target."""
    norm = _normalize_repo_path(path)
    if not norm:
        return False
    for req in path_needles:
        if _path_matches_required(req, norm):
            return True
    leaf = Path(norm).name
    stem = Path(leaf).stem
    low_path = norm.lower()
    for sym in symbol_needles:
        s = (sym or "").strip()
        if not s:
            continue
        sl = s.lower()
        s_leaf = sl.split("::")[-1].split("/")[-1]
        if not s_leaf:
            continue
        if s_leaf == leaf.lower() or s_leaf == stem.lower():
            return True
        if "/" in sl or "." in s_leaf:
            if _path_matches_required(s, norm):
                return True
        # Strong symbol as a path segment — require a path boundary so a
        # checklist mention of ``debugValue`` does not bless ``debugValue.ts``
        # edits that are unrelated to the named product files.
        if len(s_leaf) >= 4 and (
            f"/{s_leaf}." in f"/{low_path}"
            or f"/{s_leaf}/" in f"/{low_path}/"
            or low_path.startswith(s_leaf + ".")
        ):
            return True
    return False


def off_scope_edits(
    edited_files: Iterable[str],
    checklist: TaskChecklist,
) -> List[str]:
    """Edited paths not covered by any checklist product path or investigation target.

    If the checklist names no scope at all, returns [] (cannot judge off-scope).
    """
    path_needles, symbol_needles = checklist_scope(checklist)
    if not path_needles and not symbol_needles:
        return []
    out: List[str] = []
    for f in edited_files or []:
        if not file_in_checklist_scope(f, path_needles, symbol_needles):
            out.append(str(f))
    return out


def status_snapshot(checklist: Optional[TaskChecklist]) -> dict[str, str]:
    if not checklist:
        return {}
    return {item.id: item.status for item in (checklist.items or [])}


def checklist_progressed(
    before: dict[str, str],
    checklist: Optional[TaskChecklist],
) -> bool:
    """True if any previously-open item's status rank increased."""
    if not checklist or not before:
        return False
    for item in checklist.items or []:
        prev = before.get(item.id)
        if prev is None:
            continue
        if prev not in _OPEN_STATUSES:
            continue
        if STATUS_RANK.get(item.status, 0) > STATUS_RANK.get(prev, 0):
            return True
    return False


def unresolved_items(checklist: TaskChecklist) -> List[RequirementItem]:
    return [
        item
        for item in (checklist.items or [])
        if item.status in _OPEN_STATUSES and item.status != STATUS_FULLY
    ]


def detect_drift(
    history: Sequence[ReflectionTurn],
    checklist: TaskChecklist,
    *,
    min_reflections: int = 2,
) -> Optional[DriftSignal]:
    """Flag drift when the last 2 reflections were off-scope with no progress."""
    if len(history) < min_reflections:
        return None
    window = list(history[-min_reflections:])
    if any(turn.progressed for turn in window):
        return None
    # Each turn must have edited something, and those edits must be entirely off-scope
    combined_off: List[str] = []
    for turn in window:
        if not turn.files:
            return None
        off = list(turn.off_scope)
        if not off:
            return None
        # Any in-scope edit in the window means work may still be on-task
        if any(
            f not in set(off) for f in turn.files
        ):
            return None
        for f in off:
            if f not in combined_off:
                combined_off.append(f)
    open_items = unresolved_items(checklist)
    if not open_items:
        return None
    open_preview = "; ".join(
        (item.text or "").strip().replace("\n", " ")[:80] for item in open_items[:3]
    )
    files_preview = ", ".join(combined_off[:6])
    summary = (
        f"Possible drift: the last {min_reflections} reflections touched "
        f"{files_preview} without resolving {open_preview}."
    )
    return DriftSignal(
        off_scope_files=combined_off,
        unresolved=open_items,
        summary=summary,
    )


def format_refocus_message(signal: DriftSignal) -> str:
    """Replace the current reflection thread with still-open checklist work."""
    lines = [
        "Drift detected — stop the current tangent and refocus on the original task.",
        "",
        "Still-unresolved checklist items (address these):",
    ]
    for item in signal.unresolved[:8]:
        text = (item.text or "").strip()
        if len(text) > 240:
            text = text[:240] + "…"
        lines.append(f"- [{item.status}] {text}")
    if signal.off_scope_files:
        lines.append("")
        lines.append(
            "Recent off-scope edits (do not continue these unless required above): "
            + ", ".join(signal.off_scope_files[:8])
        )
    lines.append("")
    lines.append(
        "Make concrete progress on the unresolved items above. "
        "Do not spend this turn on unrelated refactors or cosmetic tweaks."
    )
    return "\n".join(lines)


def confirm_prompt(signal: DriftSignal) -> str:
    files = ", ".join(signal.off_scope_files[:4]) or "off-scope files"
    items = "; ".join(
        (i.text or "").strip().replace("\n", " ")[:60] for i in signal.unresolved[:3]
    ) or "open checklist items"
    return (
        f"Possible drift: the last 2 reflections touched {files} "
        f"without resolving {items}. Refocus on the original task instead?"
    )


def make_drift_observed_node(
    signal: DriftSignal,
    *,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    session_id: Optional[str] = None,
) -> UncertaintyNode:
    """Medium node when the human declines / --yes-always defaults to no."""
    open_texts = [i.text for i in signal.unresolved[:6]]
    return UncertaintyNode(
        title="Drift observed, continuing anyway",
        type=NodeType.REQUIREMENT_GAP,
        confidence_tier=Tier.MEDIUM,
        risk_tier=Tier.MEDIUM,
        summary=signal.summary,
        explanation=(
            "Consecutive reflections edited files outside the checklist scope "
            "while unresolved requirements stayed stuck. The operator chose to "
            "continue without refocusing."
        ),
        files_affected=list(signal.off_scope_files[:20]),
        why_uncertain=(
            "Attention may be fixed on an adjacent tangent instead of the "
            "assigned checklist items."
        ),
        what_could_go_wrong=(
            "Reflection budget is burned on unrequested work; the assigned "
            "task remains unfinished when the cap hits."
        ),
        suggested_fix=(
            "Refocus edits on the still-open checklist items before further "
            "tangential changes."
        ),
        suggested_prompt=format_refocus_message(signal),
        status=NodeStatus.NEEDS_HUMAN_REVIEW,
        task_id=task_id,
        task_title=task_title,
        created_by_session=session_id,
        signals={
            "drift_observed": True,
            "drift_continued": True,
            "off_scope_files": list(signal.off_scope_files[:20]),
            "unresolved_items": open_texts,
        },
    )
