"""Gated planning stage for high-stakes / high-blast-radius tasks.

Reuses existing high_stakes_hit / blast-radius triage — no parallel risk system.
For routine tasks this module is a no-op so the direct-to-diff path stays fast.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence, Tuple

from .established_solutions import EstablishedSolutionConsideration
from .risk import DetectionSignals, collect_base_signals, scan_high_stakes
from .schema import (
    DEFAULT_BLAST_RADIUS_THRESHOLD,
    RequirementItem,
    TaskChecklist,
    text_looks_high_stakes,
    text_looks_migration,
)


@dataclass
class ValidationContract:
    """Explicit validation contract for one public input (Codex #1)."""

    input_name: str
    domain: str
    on_invalid: str = "raise ValueError"


@dataclass
class AmbiguityResolution:
    """Named ambiguity with a chosen resolution (Codex #10)."""

    ambiguity: str
    resolution: str


@dataclass
class PlanningArtifact:
    """Human-reviewable plan produced before any diff is written."""

    task_id: str
    title: str
    reason: str = ""
    validation_contracts: List[ValidationContract] = field(default_factory=list)
    # rows: (name, domain, notes)
    input_domain_table: List[Tuple[str, str, str]] = field(default_factory=list)
    invariants: List[str] = field(default_factory=list)
    ambiguities: List[AmbiguityResolution] = field(default_factory=list)
    # Mandatory for non-trivial / established-solution categories: name the
    # stdlib/known approach, or justify a custom implementation.
    established_solutions: List[EstablishedSolutionConsideration] = field(
        default_factory=list
    )
    approved: bool = False
    skipped: bool = False  # True when triage said planning not required

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "reason": self.reason,
            "approved": self.approved,
            "skipped": self.skipped,
            "validation_contracts": [asdict(c) for c in self.validation_contracts],
            "input_domain_table": [
                {"name": n, "domain": d, "notes": note}
                for n, d, note in self.input_domain_table
            ],
            "invariants": list(self.invariants),
            "ambiguities": [asdict(a) for a in self.ambiguities],
            "established_solutions": [asdict(e) for e in self.established_solutions],
        }


_PUBLIC_INPUT_RE = re.compile(
    r"(?i)\b("
    r"timeout|retries?|max[_ ]?\w+|min[_ ]?\w+|limit|ttl|threshold|tolerance|"
    r"capacity|batch[_ ]?size|workers?|concurrency|port|rate[_ ]?limit|"
    r"api[_ ]?key|token|password|secret|credential|permission|role|"
    r"amount|price|quantity|email|url|path|filename"
    r")\b"
)
_AMBIGUOUS_RE = re.compile(
    r"(?i)\b(somehow|maybe|probably|appropriate|reasonable|as needed|"
    r"etc\.?|and so on|handle (?:it|this|errors?)|make it work|"
    r"similar to|like before|whatever|flexible)\b"
)


def planning_disabled() -> bool:
    return os.environ.get("Z_SKIP_PLANNING", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def planning_forced() -> bool:
    return os.environ.get("Z_FORCE_PLANNING", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _blast_threshold(explicit: Optional[int] = None) -> int:
    if explicit is not None:
        return max(1, int(explicit))
    raw = os.environ.get("Z_BLAST_RADIUS_THRESHOLD", "")
    try:
        return max(1, int(raw)) if raw else DEFAULT_BLAST_RADIUS_THRESHOLD
    except ValueError:
        return DEFAULT_BLAST_RADIUS_THRESHOLD


def triage_for_planning(
    files: Sequence[str],
    *,
    symbols: Sequence[str] = (),
    user_text: str = "",
    reference_count: int = 0,
    blast_radius_threshold: Optional[int] = None,
) -> Tuple[bool, str, DetectionSignals]:
    """
    Decide whether a planning artifact is required before code generation.

    Reuses scan_high_stakes / text_looks_high_stakes / blast_radius_threshold —
    the same signals the uncertainty engine already computes post-edit.
    """
    threshold = _blast_threshold(blast_radius_threshold)
    signals = collect_base_signals(
        files, symbols, blast_radius_threshold=threshold
    )
    signals.reference_count = int(reference_count or 0)

    if planning_disabled():
        return False, "Z_SKIP_PLANNING", signals

    reasons: List[str] = []
    if planning_forced():
        reasons.append("Z_FORCE_PLANNING")
    if signals.high_stakes_hit or signals.migration_hit:
        reasons.append("high_stakes_hit" if signals.high_stakes_hit else "migration_hit")
    if user_text and (
        text_looks_high_stakes(user_text) or text_looks_migration(user_text)
    ):
        if "high_stakes_hit" not in reasons and "migration_hit" not in reasons:
            reasons.append("request_text_high_stakes")
        signals.high_stakes_hit = signals.high_stakes_hit or text_looks_high_stakes(
            user_text
        )
        signals.migration_hit = signals.migration_hit or text_looks_migration(user_text)
    if signals.reference_count >= threshold:
        reasons.append(
            f"blast_radius:{signals.reference_count}>={threshold}"
        )

    # Also treat chat-file symbols that scan as high-stakes
    if not reasons and scan_high_stakes(files, symbols):
        reasons.append("high_stakes_hit")
        signals.high_stakes_hit = True

    # Established-solution categories (IP/email/URL/date/UUID/…) — require the
    # "name the standard or justify custom" plan section before inventing one.
    from .established_solutions import match_request_categories

    est_cats = match_request_categories(user_text or "")
    if est_cats:
        ids = ",".join(c.category_id for c in est_cats[:6])
        reasons.append(f"established_solution:{ids}")

    if not reasons:
        return False, "", signals
    return True, "; ".join(reasons), signals


def _extract_public_inputs(user_text: str, checklist: Optional[TaskChecklist]) -> List[str]:
    blob = user_text or ""
    if checklist:
        blob += "\n" + "\n".join(i.text for i in checklist.items)
    found: List[str] = []
    seen = set()
    for m in _PUBLIC_INPUT_RE.finditer(blob):
        name = m.group(1).lower().replace(" ", "_")
        if name not in seen:
            seen.add(name)
            found.append(name)
    return found[:12]


def _default_domain(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("timeout", "ttl", "retries", "limit", "max", "min", "port", "rate", "size", "workers", "concurrency", "amount", "price", "quantity", "threshold", "tolerance", "capacity")):
        return "finite number in documented range; reject <= 0 / NaN where applicable"
    if any(k in n for k in ("email",)):
        return "non-empty valid email string"
    if any(k in n for k in ("url", "path", "filename")):
        return "non-empty path/URL string; reject traversal / empty"
    if any(k in n for k in ("api_key", "token", "password", "secret", "credential")):
        return "non-empty secret string; never log plaintext"
    if any(k in n for k in ("permission", "role")):
        return "explicit allow-list of roles/permissions"
    return "documented non-empty domain; reject invalid immediately"


def draft_plan_from_request(
    user_message: str,
    *,
    title: str = "",
    checklist: Optional[TaskChecklist] = None,
    reason: str = "",
    files: Sequence[str] = (),
) -> PlanningArtifact:
    """
    Build a mechanical planning skeleton from the request + checklist.

    No LLM required — humans review/correct before diffs are written.
    """
    task_id = (checklist.task_id if checklist else None) or str(uuid.uuid4())
    title = title or (checklist.title if checklist else "") or "Task plan"
    inputs = _extract_public_inputs(user_message, checklist)

    contracts = [
        ValidationContract(
            input_name=name,
            domain=_default_domain(name),
            on_invalid="raise ValueError (fail loud at construction / entry)",
        )
        for name in inputs
    ]
    table = [
        (c.input_name, c.domain, "public input inferred from request")
        for c in contracts
    ]

    invariants: List[str] = []
    if checklist:
        for item in checklist.items:
            kind = getattr(item, "kind", "product") or "product"
            if kind in ("product", "quality") and item.text.strip():
                invariants.append(item.text.strip())
    if not invariants and user_message.strip():
        # Fallback: first sentence-ish of the request
        first = re.split(r"[.\n]", user_message.strip())[0].strip()
        if first:
            invariants.append(first[:200])

    # Always name fail-loud / no-fabrication when high-stakes triage fired
    boilerplate = [
        "Invalid public inputs are rejected at the boundary (no limp-forward defaults).",
        "Do not fabricate local stand-ins for missing third-party packages.",
    ]
    for b in boilerplate:
        if b not in invariants:
            invariants.append(b)

    ambiguities: List[AmbiguityResolution] = []
    for m in _AMBIGUOUS_RE.finditer(user_message or ""):
        phrase = m.group(0)
        ambiguities.append(
            AmbiguityResolution(
                ambiguity=f"Request uses vague phrasing: '{phrase}'",
                resolution=(
                    "Treat as requiring an explicit, testable behavior; "
                    "prefer fail-loud over silent fallback."
                ),
            )
        )
    if files:
        ambiguities.append(
            AmbiguityResolution(
                ambiguity=f"Edit scope includes: {', '.join(list(files)[:8])}",
                resolution="Limit changes to named files unless a dependency forces a wider edit.",
            )
        )
    if not ambiguities:
        ambiguities.append(
            AmbiguityResolution(
                ambiguity="No explicit edge-case list in the request",
                resolution=(
                    "Enumerate empty/zero/negative/null inputs for each public "
                    "parameter and add at least one rejecting test."
                ),
            )
        )

    from .established_solutions import considerations_from_text

    blob_for_est = user_message or ""
    if checklist:
        blob_for_est += "\n" + "\n".join(i.text for i in checklist.items)
    established = considerations_from_text(blob_for_est)

    # Always surface the established-solution question on gated plans so it
    # cannot be silently skipped under pressure — even when no category matched.
    if not established:
        established = [
            EstablishedSolutionConsideration(
                category_id="general",
                problem_category=(
                    "Any well-known problem (parsing, data structure, concurrency, …)"
                ),
                standard_approach="",
                decision="unspecified",
                custom_justification="",
            )
        ]

    est_invariant = (
        "For each established-solution category: use the named standard "
        "approach (stdlib / known algorithm), or record an explicit custom "
        "justification — do not invent a parser/structure from scratch silently."
    )
    if est_invariant not in invariants:
        invariants.append(est_invariant)

    return PlanningArtifact(
        task_id=task_id,
        title=title,
        reason=reason,
        validation_contracts=contracts,
        input_domain_table=table,
        invariants=invariants[:16],
        ambiguities=ambiguities[:8],
        established_solutions=established[:8],
        approved=False,
        skipped=False,
    )


def format_plan_for_user(plan: PlanningArtifact) -> str:
    lines = [
        f"High-stakes plan (required before edits): {plan.title}",
        f"Trigger: {plan.reason or '(unspecified)'}",
        "",
        "Validation contracts (per public input):",
    ]
    if plan.validation_contracts:
        for c in plan.validation_contracts:
            lines.append(
                f"  - {c.input_name}: domain={c.domain}; on_invalid={c.on_invalid}"
            )
    else:
        lines.append("  - (none inferred — add any new public inputs explicitly)")

    lines.append("")
    lines.append("Input-domain table:")
    if plan.input_domain_table:
        lines.append("  | name | domain | notes |")
        for name, domain, notes in plan.input_domain_table:
            lines.append(f"  | {name} | {domain} | {notes} |")
    else:
        lines.append("  (empty)")

    lines.append("")
    lines.append("Named invariants:")
    for inv in plan.invariants:
        lines.append(f"  - {inv}")

    lines.append("")
    lines.append("Ambiguities → chosen resolution:")
    for a in plan.ambiguities:
        lines.append(f"  - {a.ambiguity}")
        lines.append(f"    → {a.resolution}")

    lines.append("")
    lines.append(
        "Established solutions (required — name the standard approach, "
        "or justify custom):"
    )
    if plan.established_solutions:
        for e in plan.established_solutions:
            lines.append(f"  - [{e.category_id}] {e.problem_category}")
            if e.standard_approach:
                lines.append(f"      Prefer: {e.standard_approach}")
            lines.append(
                "      Decision: use standard (name it) OR custom because: …"
            )
            if e.decision and e.decision != "unspecified":
                lines.append(f"      Recorded: {e.decision}")
                if e.custom_justification:
                    lines.append(f"      Justification: {e.custom_justification}")
    else:
        lines.append(
            "  - (none inferred — still ask: is any part a solved stdlib/"
            "algorithm problem?)"
        )

    lines.append("")
    lines.append(
        "Confirm this plan to proceed. Reject to stop before any diff is written."
    )
    return "\n".join(lines)


def format_plan_for_context(plan: PlanningArtifact) -> str:
    """Inject into cur_messages as grounding for implementation + detectors."""
    lines = [
        "# Approved implementation plan (binding)",
        f"Task: {plan.title}",
        f"Why gated: {plan.reason}",
        "",
        "## Validation contracts",
    ]
    for c in plan.validation_contracts:
        lines.append(f"- `{c.input_name}`: {c.domain} / {c.on_invalid}")
    if not plan.validation_contracts:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Input-domain table")
    for name, domain, notes in plan.input_domain_table:
        lines.append(f"- {name}: {domain} ({notes})")
    lines.append("")
    lines.append("## Invariants (must hold in the diff)")
    for inv in plan.invariants:
        lines.append(f"- {inv}")
    lines.append("")
    lines.append("## Ambiguity resolutions")
    for a in plan.ambiguities:
        lines.append(f"- {a.ambiguity} → {a.resolution}")
    lines.append("")
    lines.append("## Established solutions (binding)")
    lines.append(
        "If the task involves a well-known problem (IP/email/URL/date/UUID "
        "parsing, heaps/caches, concurrency primitives), use the language "
        "stdlib or a known algorithm. Inventing a custom parser/structure "
        "requires an explicit custom justification in this plan."
    )
    for e in plan.established_solutions:
        prefer = e.standard_approach or "(name the standard)"
        lines.append(
            f"- [{e.category_id}] {e.problem_category}: prefer `{prefer}` "
            f"(decision={e.decision or 'unspecified'})"
        )
        if e.custom_justification:
            lines.append(f"  custom justification: {e.custom_justification}")
    lines.append("")
    lines.append(
        "Implement only what this plan authorizes. "
        "Detectors will check the diff against these invariants and against "
        "the established-solutions taxonomy."
    )
    return "\n".join(lines)


def merge_plan_invariants_into_checklist(
    checklist: TaskChecklist,
    plan: PlanningArtifact,
) -> TaskChecklist:
    """
    Fold named invariants into the requirement ledger as quality items so
    bind_evidence / detect_requirement_gaps can check them mechanically.
    """
    if not checklist or not plan or not plan.invariants:
        return checklist
    existing = {i.text.strip().lower() for i in checklist.items}
    for inv in plan.invariants:
        key = inv.strip().lower()
        if not key or key in existing:
            continue
        # Skip boilerplate that is process-style guidance
        kind = "quality"
        if "fabricate" in key or "third-party" in key:
            kind = "process"
        checklist.items.append(
            RequirementItem(
                text=inv.strip(),
                kind=kind,
                status="Not Addressed",
            )
        )
        existing.add(key)
    return checklist


def plan_invariant_texts(plan: Optional[PlanningArtifact]) -> List[str]:
    if not plan or plan.skipped or not plan.approved:
        return []
    return [i for i in plan.invariants if i and i.strip()]
