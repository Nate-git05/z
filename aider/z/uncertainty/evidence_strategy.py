"""Fail-closed evidence strategies for requirement checklist scoring.

Architectural fix for the "enumerate a detector after each miss" loop:

  Philosophy (schema.py): never trust self-rated confidence — only concrete signals.
  Before: that philosophy was applied by growing a list of special cases; any kind
  without a binder silently passed on model/keyword vibes.
  After: Fully Addressed is allowed only when a registered strategy's checkable
  predicate is true. Unknown kinds and model self-reports cannot raise status
  above mechanical evidence (fail closed by default).
"""

from __future__ import annotations

from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

# Import ItemEvidence lazily-typed to avoid circular imports at module load
STATUS_NOT = "Not Addressed"
STATUS_PARTIAL = "Partially Addressed"
STATUS_FULLY = "Fully Addressed"

STATUS_RANK = {
    STATUS_NOT: 0,
    STATUS_PARTIAL: 1,
    STATUS_FULLY: 2,
}

# Only these kinds have a registered concrete-signal strategy.
# Anything else fails closed → Not Addressed (never silent product vibes).
REGISTERED_KINDS: FrozenSet[str] = frozenset(
    {
        "product",
        "quality",
        "verification",
        "documentation",
        "process",
        "decision",
        "external_assumption",
    }
)


def normalize_kind(kind: Optional[str]) -> str:
    k = (kind or "product").strip().lower()
    return k or "product"


def is_registered_kind(kind: Optional[str]) -> bool:
    return normalize_kind(kind) in REGISTERED_KINDS


def allows_fully(ev, words: Sequence[str] = ()) -> bool:
    """
    True only when checkable evidence for this kind is present.

    This is the gate for Fully Addressed — not keyword vibes, not model opinion.
    """
    kind = normalize_kind(getattr(ev, "kind", None))
    if kind not in REGISTERED_KINDS:
        return False
    if kind == "verification":
        return getattr(ev, "verification_ok", None) is True
    if kind == "documentation":
        # docs_touched this turn + some content signal
        notes = getattr(ev, "evidence_notes", None) or []
        touched = any(n == "docs_touched:true" for n in notes) or bool(
            getattr(ev, "file_hits", None)
        )
        return bool(touched) and (
            bool(getattr(ev, "keyword_hits", None))
            or any(str(n).startswith("doc:") for n in notes)
        )
    if kind == "process":
        return bool(getattr(ev, "has_process_evidence", False))
    if kind == "decision":
        # Concrete user decision markers only — not ambient process log hits
        return bool(getattr(ev, "decision_hits", None))
    if kind == "external_assumption":
        return bool(getattr(ev, "log_hits", None))
    if kind in ("product", "quality"):
        return bool(getattr(ev, "has_hard_product_evidence", False))
    return False


def status_from_strategy(ev, words: Sequence[str] = ()) -> str:
    """
    Mechanical status from registered strategy. Unknown kinds → Not Addressed.
    """
    kind = normalize_kind(getattr(ev, "kind", None))
    words = list(words or [])

    if kind not in REGISTERED_KINDS:
        # Fail closed: do not fall through to product keyword Partial
        if hasattr(ev, "evidence_notes"):
            notes = list(ev.evidence_notes or [])
            if "unsupported_kind:fail_closed" not in notes:
                notes.append("unsupported_kind:fail_closed")
                ev.evidence_notes = notes
        return STATUS_NOT

    if kind == "process":
        return STATUS_FULLY if allows_fully(ev, words) else STATUS_NOT

    if kind == "verification":
        vok = getattr(ev, "verification_ok", None)
        if vok is True:
            return STATUS_FULLY
        if vok is False:
            return STATUS_PARTIAL
        return STATUS_NOT

    if kind == "decision":
        return STATUS_FULLY if allows_fully(ev, words) else STATUS_NOT

    if kind == "documentation":
        if allows_fully(ev, words):
            return STATUS_FULLY
        if getattr(ev, "has_doc_evidence", False):
            return STATUS_PARTIAL
        return STATUS_NOT

    if kind == "external_assumption":
        return STATUS_FULLY if allows_fully(ev, words) else STATUS_NOT

    if kind == "quality":
        if allows_fully(ev, words):
            return STATUS_FULLY
        if getattr(ev, "test_hits", None) or getattr(ev, "has_code_evidence", False):
            return STATUS_PARTIAL
        return STATUS_NOT

    # product
    if allows_fully(ev, words):
        return STATUS_FULLY
    if not words:
        if getattr(ev, "has_code_evidence", False) or getattr(
            ev, "has_test_only_evidence", False
        ):
            return STATUS_PARTIAL
        return STATUS_NOT
    keyword_hits = set(getattr(ev, "keyword_hits", None) or [])
    test_hits = getattr(ev, "test_hits", None) or []
    distinctive = keyword_hits | {
        w for w in words if any(w in t.lower() for t in test_hits)
    }
    hit_ratio = len(distinctive) / max(len(words), 1)
    if getattr(ev, "has_test_only_evidence", False):
        return (
            STATUS_PARTIAL
            if hit_ratio >= 0.25 or test_hits
            else STATUS_NOT
        )
    if (
        hit_ratio >= 0.25
        or getattr(ev, "has_code_evidence", False)
        or test_hits
    ):
        return STATUS_PARTIAL
    return STATUS_NOT


def combine_model_and_mechanical(
    mechanical: str,
    model: str,
    *,
    ev=None,
) -> Tuple[str, bool]:
    """
    Fail-closed combine: model may never raise status above mechanical evidence.

    Returns (final_status, model_was_ceilinged).
    Strong mechanical Fully is also a floor (evidence wins over model downgrade).
    """
    if mechanical not in STATUS_RANK:
        mechanical = STATUS_NOT
    if model not in STATUS_RANK:
        model = mechanical

    ceilinged = False
    if STATUS_RANK[model] > STATUS_RANK[mechanical]:
        ceilinged = True
        final = mechanical
    elif mechanical == STATUS_FULLY:
        # Checkable evidence present — model cannot talk it down
        final = STATUS_FULLY
    else:
        final = model

    if ceilinged and ev is not None:
        notes = list(getattr(ev, "evidence_notes", None) or [])
        note = "model_claimed_above_mechanical_evidence"
        if note not in notes:
            notes.append(note)
            ev.evidence_notes = notes
        if hasattr(ev, "missing"):
            ev.missing = (
                (ev.missing or "")
                + (
                    f" Model claimed {model} but mechanical evidence only supports "
                    f"{mechanical}."
                )
            ).strip()
    return final, ceilinged


def missing_message_for(ev, words: Sequence[str] = ()) -> str:
    """Default missing text when strategy leaves an open gap."""
    kind = normalize_kind(getattr(ev, "kind", None))
    text = getattr(ev, "item_text", "") or ""
    if kind not in REGISTERED_KINDS:
        return (
            f"Unsupported requirement kind '{kind}' — fail closed until a concrete "
            f"evidence strategy is registered: {text}"
        )
    if kind == "process":
        return f"No session/execution evidence for process requirement: {text}"
    if kind == "verification":
        return f"Verification not yet satisfied for: {text}"
    if kind == "decision":
        return f"User decision still needed: {text}"
    if kind == "documentation":
        return (
            f"No README/CHANGELOG/docs/** edited this turn for: {text}"
        )
    if kind == "external_assumption":
        return f"External assumption unverified: {text}"
    if kind == "quality":
        parts = []
        if hasattr(ev, "missing_hard_evidence_parts"):
            parts = ev.missing_hard_evidence_parts()
        return (
            f"Quality requirement needs file+symbol+test evidence "
            f"(missing: {', '.join(parts) or 'all'}): {text}"
        )
    parts = []
    if hasattr(ev, "missing_hard_evidence_parts"):
        parts = ev.missing_hard_evidence_parts()
    return (
        f"No complete evidence (need file+symbol+test; missing: "
        f"{', '.join(parts) or 'all'}) for: {text}"
    )
