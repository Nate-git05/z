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
    # Item ids whose file/symbol evidence did not grow this turn
    stagnant_ids: Set[str] = field(default_factory=set)


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


def _evidence_fingerprint(item: RequirementItem) -> Optional[frozenset]:
    """file_hits ∪ symbol_hits from the item's cached last_evidence, if any."""
    ev = getattr(item, "last_evidence", None)
    if ev is None:
        return None
    files = frozenset(str(x) for x in (getattr(ev, "file_hits", None) or []) if x)
    symbols = frozenset(str(x) for x in (getattr(ev, "symbol_hits", None) or []) if x)
    return files | symbols


def evidence_snapshot(checklist: Optional[TaskChecklist]) -> dict[str, frozenset]:
    """Fingerprint each item's evidence (file/symbol hits) for stagnation comparison."""
    if not checklist:
        return {}
    out: dict[str, frozenset] = {}
    for item in checklist.items or []:
        fp = _evidence_fingerprint(item)
        if fp is None:
            continue
        out[item.id] = fp
    return out


def evidence_stagnant(
    before: dict[str, frozenset],
    checklist: Optional[TaskChecklist],
) -> Set[str]:
    """Item ids whose evidence set did not grow since ``before``."""
    stagnant: Set[str] = set()
    if not checklist:
        return stagnant
    for item in checklist.items or []:
        current = _evidence_fingerprint(item)
        if current is None:
            continue
        prev = before.get(item.id)
        if prev is not None and current <= prev:
            stagnant.add(item.id)
    return stagnant


def multi_turn_stagnant(
    history: Sequence[ReflectionTurn],
    current_stagnant: Set[str],
    *,
    window: int = 2,
) -> Set[str]:
    """Item ids stagnant across the last ``window`` reflections (incl. current)."""
    if window <= 1:
        return set(current_stagnant)
    prior = list(history[-(window - 1) :])
    if len(prior) < window - 1:
        return set()
    common = set(current_stagnant)
    for turn in prior:
        common &= set(getattr(turn, "stagnant_ids", None) or ())
    return common


def checklist_scope(
    checklist: TaskChecklist,
    *,
    open_only: bool = True,
    stagnant_ids: Optional[Set[str]] = None,
) -> Tuple[List[str], List[str]]:
    """Named file paths + usable investigation symbols from checklist items.

    With ``open_only=True`` (default, used by drift), Fully Addressed items are
    skipped so a resolved file is no longer a permanent in-scope anchor.
    ``stagnant_ids`` extends that: items whose evidence has not grown for the
    configured reflection window are also excluded — so a Partial-forever item
    (no test framework → never Fully) stops anchoring scope once nothing new
    is happening. If rescoring later grows evidence, the id leaves stagnant
    and the file re-enters scope on the next call.

    Bare weak tokens from ``extract_investigation_targets`` (e.g. sentence
    starters) are ignored so a vague item does not invent fake scope.
    """
    paths: List[str] = []
    symbols: List[str] = []
    skip_ids = set(stagnant_ids or ())
    for item in checklist.items or []:
        if open_only:
            if (item.status or "").strip() == STATUS_FULLY:
                continue
            if item.id in skip_ids:
                continue
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
    *,
    stagnant_ids: Optional[Set[str]] = None,
) -> List[str]:
    """Edited paths not covered by any still-active checklist item's scope.

    Scope comes from open + non-stagnant items (see ``checklist_scope``).

    - Active items name paths/symbols → flag edits outside that set.
    - Active items name nothing, but *resolved/stagnant* items did → every
      edit is off-scope (LRU refactor-creep after the fix stopped producing
      new evidence, even if stuck at Partial for lack of tests).
    - Checklist never named any scope → return [] (cannot judge).
    """
    path_needles, symbol_needles = checklist_scope(
        checklist, open_only=True, stagnant_ids=stagnant_ids
    )
    if path_needles or symbol_needles:
        out: List[str] = []
        for f in edited_files or []:
            if not file_in_checklist_scope(f, path_needles, symbol_needles):
                out.append(str(f))
        return out

    # No active scope — only flag when *some* checklist item named
    # files/symbols, so vague all-open checklists still return [] (cannot judge).
    any_paths, any_syms = checklist_scope(checklist, open_only=False)
    if not any_paths and not any_syms:
        return []
    # Resolved/stagnant items named scope; nothing active does → all edits drift
    return [str(f) for f in (edited_files or [])]


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
    min_reflections: int = 1,
) -> Optional[DriftSignal]:
    """Flag drift when recent reflection(s) were off-scope with no progress.

    Default window is 1 so a single qualifying reflection can fire inside a
    ``max_reflections=3`` budget (the first rescore turn often has empty
    off_scope by construction; waiting for two consecutive qualifiers made
    the detector unreachable before exhaustion).
    """
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
    files_preview = ", ".join(combined_off[:6])
    n = min_reflections
    refl_label = "reflection" if n == 1 else "reflections"
    if open_items:
        open_preview = "; ".join(
            (item.text or "").strip().replace("\n", " ")[:80]
            for item in open_items[:3]
        )
        summary = (
            f"Possible drift: the last {n} {refl_label} touched "
            f"{files_preview} without resolving {open_preview}."
        )
    else:
        # Task already complete — unprompted scope-creep after the fix landed
        summary = (
            f"Possible drift: the last {n} {refl_label} touched "
            f"{files_preview}, but every checklist item is already resolved — "
            "this looks like unrequested extra work."
        )
    return DriftSignal(
        off_scope_files=combined_off,
        unresolved=open_items,
        summary=summary,
    )


def is_complete_task_creep(signal: DriftSignal) -> bool:
    """True when drift is post-completion scope-creep (nothing left to refocus on)."""
    return not bool(signal.unresolved)


def format_refocus_message(signal: DriftSignal) -> Optional[str]:
    """Message for the next reflection, or None when accept means stop-here."""
    if is_complete_task_creep(signal):
        # Accepting "stop here" must not push another reflection turn.
        return None
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
    if is_complete_task_creep(signal):
        return (
            f"Possible drift: recent reflection(s) touched {files}, but the "
            "task appears complete — these edits go beyond what was requested. "
            "Stop here?"
        )
    items = "; ".join(
        (i.text or "").strip().replace("\n", " ")[:60] for i in signal.unresolved[:3]
    ) or "open checklist items"
    return (
        f"Possible drift: recent reflection(s) touched {files} "
        f"without resolving {items}. Refocus on the original task instead?"
    )


@dataclass(frozen=True)
class DriftConfirmResult:
    """Outcome of the confirm gate after drift is flagged."""

    # Accept + open items → rewrite next reflection
    refocus_message: Optional[str] = None
    # Accept + checklist complete → end the reflection loop
    stop: bool = False


def make_drift_observed_node(
    signal: DriftSignal,
    *,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    session_id: Optional[str] = None,
) -> UncertaintyNode:
    """Medium node when the human declines / --yes-always defaults to no."""
    open_texts = [i.text for i in signal.unresolved[:6]]
    complete = is_complete_task_creep(signal)
    if complete:
        explanation = (
            "Consecutive reflections edited files after every checklist item "
            "was already resolved. The operator chose to continue the "
            "unrequested extra work."
        )
        why = (
            "The assigned task looks done, but reflections kept changing "
            "code beyond what was requested."
        )
        wrong = (
            "Unprompted refactors and cosmetic tweaks burn the reflection "
            "budget and risk regressing a finished fix."
        )
        fix = "Stop editing and end the turn — the checklist is already complete."
        suggested = (
            "The task appears complete. Do not continue unrequested edits to "
            + ", ".join(signal.off_scope_files[:6])
        )
    else:
        explanation = (
            "Consecutive reflections edited files outside the checklist scope "
            "while unresolved requirements stayed stuck. The operator chose to "
            "continue without refocusing."
        )
        why = (
            "Attention may be fixed on an adjacent tangent instead of the "
            "assigned checklist items."
        )
        wrong = (
            "Reflection budget is burned on unrequested work; the assigned "
            "task remains unfinished when the cap hits."
        )
        fix = (
            "Refocus edits on the still-open checklist items before further "
            "tangential changes."
        )
        suggested = format_refocus_message(signal) or signal.summary
    return UncertaintyNode(
        title="Drift observed, continuing anyway",
        type=NodeType.REQUIREMENT_GAP,
        confidence_tier=Tier.MEDIUM,
        risk_tier=Tier.MEDIUM,
        summary=signal.summary,
        explanation=explanation,
        files_affected=list(signal.off_scope_files[:20]),
        why_uncertain=why,
        what_could_go_wrong=wrong,
        suggested_fix=fix,
        suggested_prompt=suggested,
        status=NodeStatus.NEEDS_HUMAN_REVIEW,
        task_id=task_id,
        task_title=task_title,
        created_by_session=session_id,
        signals={
            "drift_observed": True,
            "drift_continued": True,
            "complete_task_creep": complete,
            "off_scope_files": list(signal.off_scope_files[:20]),
            "unresolved_items": open_texts,
        },
    )
