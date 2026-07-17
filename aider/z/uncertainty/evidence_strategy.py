"""Exhaustive kind → verifier registry (fail closed by design).

Every RequirementItem kind must appear in KIND_VERIFIERS mapped to either:
  - a real verifier (checkable Fully/Partial/Not), or
  - an explicit absence marker (verifier=None) → status "Unverifiable"

Unverifiable is honest "we don't know" — Low/informational until that category
gets a trusted verifier promoted to hard-block (same path as tests_passed /
docs_touched). Silent pass on model self-report is not allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

STATUS_NOT = "Not Addressed"
STATUS_PARTIAL = "Partially Addressed"
STATUS_FULLY = "Fully Addressed"
STATUS_UNVERIFIABLE = "Unverifiable"

STATUS_RANK = {
    STATUS_UNVERIFIABLE: 0,
    STATUS_NOT: 1,
    STATUS_PARTIAL: 2,
    STATUS_FULLY: 3,
}

# Kinds emitted by classify_requirement_kind — registry MUST be exhaustive.
ALL_REQUIREMENT_KINDS: FrozenSet[str] = frozenset(
    {
        "product",
        "quality",
        "verification",
        "documentation",
        "process",
        "decision",
        "external_assumption",
        # Named investigative hints inside bug-fix tasks ("also check X")
        "investigation",
    }
)


@dataclass(frozen=True)
class KindVerifier:
    """One row in the exhaustive kind → verifier registry."""

    kind: str
    # Human-readable name of the concrete signal (or why absent)
    signal_name: str
    # None = explicit absence-of-verifier → Unverifiable (never silent pass)
    allows_fully: Optional[Callable[..., bool]] = None
    status_fn: Optional[Callable[..., str]] = None
    # When True, Not Addressed gaps may hard-block (trusted verifier)
    hard_block_on_gap: bool = False
    description: str = ""

    @property
    def has_verifier(self) -> bool:
        return self.allows_fully is not None and self.status_fn is not None


def normalize_kind(kind: Optional[str]) -> str:
    k = (kind or "product").strip().lower()
    return k or "product"


# --- Per-kind checkable predicates ------------------------------------------------


def _fully_verification(ev, words: Sequence[str] = ()) -> bool:
    return getattr(ev, "verification_ok", None) is True


def _status_verification(ev, words: Sequence[str] = ()) -> str:
    vok = getattr(ev, "verification_ok", None)
    if vok is True:
        return STATUS_FULLY
    if vok is False:
        return STATUS_PARTIAL
    return STATUS_NOT


def _fully_documentation(ev, words: Sequence[str] = ()) -> bool:
    notes = getattr(ev, "evidence_notes", None) or []
    touched = any(n == "docs_touched:true" for n in notes) or bool(
        getattr(ev, "file_hits", None)
    )
    return bool(touched) and (
        bool(getattr(ev, "keyword_hits", None))
        or any(str(n).startswith("doc:") for n in notes)
    )


def _status_documentation(ev, words: Sequence[str] = ()) -> str:
    if _fully_documentation(ev, words):
        return STATUS_FULLY
    if getattr(ev, "has_doc_evidence", False):
        return STATUS_PARTIAL
    return STATUS_NOT


def _fully_product(ev, words: Sequence[str] = ()) -> bool:
    return bool(getattr(ev, "has_hard_product_evidence", False))


def _status_product(ev, words: Sequence[str] = ()) -> str:
    words = list(words or [])
    if _fully_product(ev, words):
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
        return STATUS_PARTIAL if hit_ratio >= 0.25 or test_hits else STATUS_NOT
    if hit_ratio >= 0.25 or getattr(ev, "has_code_evidence", False) or test_hits:
        return STATUS_PARTIAL
    return STATUS_NOT


def _status_quality(ev, words: Sequence[str] = ()) -> str:
    if _fully_product(ev, words):
        return STATUS_FULLY
    if getattr(ev, "test_hits", None) or getattr(ev, "has_code_evidence", False):
        return STATUS_PARTIAL
    return STATUS_NOT


def _fully_process(ev, words: Sequence[str] = ()) -> bool:
    return bool(getattr(ev, "has_process_evidence", False))


def _status_process(ev, words: Sequence[str] = ()) -> str:
    return STATUS_FULLY if _fully_process(ev, words) else STATUS_NOT


def _fully_decision(ev, words: Sequence[str] = ()) -> bool:
    return bool(getattr(ev, "decision_hits", None))


def _status_decision(ev, words: Sequence[str] = ()) -> str:
    return STATUS_FULLY if _fully_decision(ev, words) else STATUS_NOT


def _investigation_disposition(ev) -> Optional[str]:
    for n in getattr(ev, "evidence_notes", None) or []:
        if str(n).startswith("disposition:"):
            return str(n).split(":", 1)[-1]
    return None


def _fully_investigation(ev, words: Sequence[str] = ()) -> bool:
    """Fully only with an explicit checked_* disposition (not model say-so)."""
    return _investigation_disposition(ev) in ("checked_fixed", "checked_ruled_out")


def _status_investigation(ev, words: Sequence[str] = ()) -> str:
    disp = _investigation_disposition(ev)
    if disp in ("checked_fixed", "checked_ruled_out"):
        return STATUS_FULLY
    if disp == "partial_inspect":
        return STATUS_PARTIAL
    return STATUS_NOT


# Exhaustive registry — every ALL_REQUIREMENT_KINDS entry must appear.
# external_assumption: explicit absence until live-API verification is trustworthy.
KIND_VERIFIERS: Dict[str, KindVerifier] = {
    "verification": KindVerifier(
        kind="verification",
        signal_name="tests_passed / verification_ok",
        allows_fully=_fully_verification,
        status_fn=_status_verification,
        # Checklist wording gaps stay Low; failing tests hard-block via verify path.
        hard_block_on_gap=False,
        description="Suite must meaningfully pass this session.",
    ),
    "documentation": KindVerifier(
        kind="documentation",
        signal_name="docs_touched",
        allows_fully=_fully_documentation,
        status_fn=_status_documentation,
        hard_block_on_gap=False,  # Medium today; promote when trusted
        description="README*/CHANGELOG*/docs/** edited this turn.",
    ),
    "product": KindVerifier(
        kind="product",
        signal_name="file+symbol+test",
        allows_fully=_fully_product,
        status_fn=_status_product,
        hard_block_on_gap=True,
        description="Implementation file, symbol, and behavioral test.",
    ),
    "quality": KindVerifier(
        kind="quality",
        signal_name="file+symbol+test",
        allows_fully=_fully_product,
        status_fn=_status_quality,
        hard_block_on_gap=False,
        description="Same hard triad; gaps are reviewable Medium.",
    ),
    "process": KindVerifier(
        kind="process",
        signal_name="execution_log / session evidence",
        allows_fully=_fully_process,
        status_fn=_status_process,
        hard_block_on_gap=False,
        description="Session/execution facts — never invent product features.",
    ),
    "decision": KindVerifier(
        kind="decision",
        signal_name="user_decision_hits",
        allows_fully=_fully_decision,
        status_fn=_status_decision,
        hard_block_on_gap=False,
        description="Explicit user decision markers only.",
    ),
    "external_assumption": KindVerifier(
        kind="external_assumption",
        signal_name="(none — live API verify not trusted yet)",
        allows_fully=None,
        status_fn=None,
        hard_block_on_gap=False,
        description=(
            "No trusted mechanical verifier yet. Requirements of this kind "
            "are Unverifiable (informational) until live-API verification ships."
        ),
    ),
    "investigation": KindVerifier(
        kind="investigation",
        signal_name="diff_touch_or_session_inspect",
        allows_fully=_fully_investigation,
        status_fn=_status_investigation,
        hard_block_on_gap=True,
        description=(
            "Named investigative hint must be checked_fixed (diff touches "
            "named symbols) or checked_ruled_out (session inspect/grep "
            "evidence) — silently skipping hard-blocks like product gaps."
        ),
    ),
}


def _assert_registry_exhaustive() -> None:
    missing = ALL_REQUIREMENT_KINDS - set(KIND_VERIFIERS)
    extra = set(KIND_VERIFIERS) - ALL_REQUIREMENT_KINDS
    if missing or extra:
        raise RuntimeError(
            f"KIND_VERIFIERS must be exhaustive for ALL_REQUIREMENT_KINDS; "
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )


_assert_registry_exhaustive()

# Back-compat alias used by earlier fail-closed patch / exports
REGISTERED_KINDS: FrozenSet[str] = frozenset(
    k for k, v in KIND_VERIFIERS.items() if v.has_verifier
)


def verifier_for(kind: Optional[str]) -> KindVerifier:
    """Return registry row; unknown kinds get an explicit absence marker."""
    k = normalize_kind(kind)
    if k in KIND_VERIFIERS:
        return KIND_VERIFIERS[k]
    return KindVerifier(
        kind=k,
        signal_name="(none — kind not in registry)",
        allows_fully=None,
        status_fn=None,
        hard_block_on_gap=False,
        description=f"Unknown kind '{k}' — Unverifiable by design.",
    )


def is_registered_kind(kind: Optional[str]) -> bool:
    """True when kind has a real verifier (not an absence marker)."""
    return verifier_for(kind).has_verifier


def allows_fully(ev, words: Sequence[str] = ()) -> bool:
    v = verifier_for(getattr(ev, "kind", None))
    if not v.has_verifier or v.allows_fully is None:
        return False
    return bool(v.allows_fully(ev, words))


def status_from_strategy(ev, words: Sequence[str] = ()) -> str:
    """
    Mechanical status from the exhaustive registry.

    No verifier → Unverifiable (honest unknown), never silent Fully/Partial.
    """
    v = verifier_for(getattr(ev, "kind", None))
    words = list(words or [])
    if not v.has_verifier or v.status_fn is None:
        if hasattr(ev, "evidence_notes"):
            notes = list(ev.evidence_notes or [])
            marker = f"unverifiable:{v.kind}"
            if marker not in notes:
                notes.append(marker)
                notes.append("absence_of_verifier")
                ev.evidence_notes = notes
        return STATUS_UNVERIFIABLE
    return v.status_fn(ev, words)


def combine_model_and_mechanical(
    mechanical: str,
    model: str,
    *,
    ev=None,
) -> Tuple[str, bool]:
    """Model may never raise above mechanical; Unverifiable is sticky."""
    if mechanical == STATUS_UNVERIFIABLE:
        return STATUS_UNVERIFIABLE, model != STATUS_UNVERIFIABLE
    if mechanical not in STATUS_RANK:
        mechanical = STATUS_NOT
    if model not in STATUS_RANK or model == STATUS_UNVERIFIABLE:
        # Model cannot invent Unverifiable away or invent ranks we don't know
        if model == STATUS_UNVERIFIABLE:
            return mechanical, False
        model = mechanical

    ceilinged = False
    if STATUS_RANK[model] > STATUS_RANK[mechanical]:
        ceilinged = True
        final = mechanical
    elif mechanical == STATUS_FULLY:
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
    v = verifier_for(getattr(ev, "kind", None))
    text = getattr(ev, "item_text", "") or ""
    if not v.has_verifier:
        return (
            f"Unverifiable — no check exists for this category yet "
            f"(kind={v.kind}; signal={v.signal_name}): {text}"
        )
    if v.kind == "process":
        return f"No session/execution evidence for process requirement: {text}"
    if v.kind == "verification":
        return f"Verification not yet satisfied for: {text}"
    if v.kind == "decision":
        return f"User decision still needed: {text}"
    if v.kind == "documentation":
        return f"No README/CHANGELOG/docs/** edited this turn for: {text}"
    if v.kind == "investigation":
        targets = []
        for n in getattr(ev, "evidence_notes", None) or []:
            if str(n).startswith("targets:"):
                targets.append(str(n).split(":", 1)[-1])
        target_s = targets[0] if targets else "(named area)"
        return (
            f"Investigation not checked — no diff touch and no session "
            f"inspect/grep evidence for {target_s}. Disposition required: "
            f"checked_fixed | checked_ruled_out. Asked: {text}"
        )
    if v.kind == "quality":
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


def hard_block_kind(kind: Optional[str]) -> bool:
    return bool(verifier_for(kind).hard_block_on_gap)
