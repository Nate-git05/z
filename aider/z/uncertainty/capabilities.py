"""Capability-selection layer above named skill selection.

Named skills are only one source of capability. Before implementation, Z
infers the capabilities needed to *prove* completion, compares them to
available skills/tools/native abilities, and records coverage gaps so the
agent compensates with its own workflow rather than treating "no skill" as
"no specialized verification needed."
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence, Set


@dataclass(frozen=True)
class Capability:
    """One capability required to implement or prove a task."""

    id: str
    label: str
    # What evidence proves this capability was exercised
    evidence_type: str
    # Why it was inferred
    reason: str = ""
    critical: bool = True
    # Provenance (P0.3) — required for every activation
    requirement_id: str = ""
    matched_span: str = ""
    confidence: float = 0.0
    supporting_requirement_ids: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ClassifiedRequirement:
    """One classified requirement clause for capability inference."""

    id: str
    text: str
    kind: str = "requested_action"  # requested_action | observation | prohibited
    polarity: str = "required"  # required | prohibited | informational


@dataclass
class CapabilityPlan:
    """Required vs available capabilities for a task."""

    required: List[Capability] = field(default_factory=list)
    available_from_skills: List[str] = field(default_factory=list)
    available_native: List[str] = field(default_factory=list)
    coverage_gaps: List[Capability] = field(default_factory=list)
    compensation: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "required": [c.to_dict() for c in self.required],
            "available_from_skills": list(self.available_from_skills),
            "available_native": list(self.available_native),
            "coverage_gaps": [c.to_dict() for c in self.coverage_gaps],
            "compensation": list(self.compensation),
        }


# (capability_id, label, evidence_type, pattern, critical)
_CAPABILITY_RULES: Sequence[tuple[str, str, str, re.Pattern[str], bool]] = (
    (
        "nextjs_impl",
        "Next.js / React implementation",
        "code_review",
        re.compile(r"(?i)\b(next\.?js|react|app\s+router|pages\s+router)\b"),
        True,
    ),
    (
        "shared_server_state",
        "Persistent / shared server state",
        "integration_test",
        re.compile(
            r"(?i)\b(multiplayer|multi-?user|shared\s+state|lobby|realtime|"
            r"web\s*socket|presence|collaborat)\b"
        ),
        True,
    ),
    (
        "multi_session_browser",
        "Two independent browser sessions",
        "multi_session_e2e",
        re.compile(
            r"(?i)\b(multiplayer|two\s+players?|player\s*[ab]|guest|host|"
            r"qr\s*code|second\s+(?:browser|client|session)|multi-?session)\b"
        ),
        True,
    ),
    (
        "api_integration",
        "API integration verification",
        "integration_test",
        re.compile(r"(?i)\b(api|endpoint|rest|graphql|route\s+handler)\b"),
        True,
    ),
    (
        "responsive_ui",
        "Responsive UI inspection",
        "browser_viewport",
        re.compile(
            r"(?i)\b(ui|frontend|page|mobile|responsive|viewport|css|layout)\b"
        ),
        False,
    ),
    (
        "auth_review",
        "Authentication / authorization review",
        "security_review",
        re.compile(
            r"(?i)\b(auth|login|signup|oauth|session|jwt|permission|role|"
            r"authorize)\b"
        ),
        True,
    ),
    (
        "accessibility",
        "Accessibility checking",
        "a11y_check",
        re.compile(r"(?i)\b(a11y|accessib|screen\s*reader|aria|wcag)\b"),
        False,
    ),
    (
        "concurrency_safety",
        "Concurrency / race safety",
        "concurrency_test",
        re.compile(
            r"(?i)\b(concurren|race|deadlock|mutex|lock|parallel|thread|"
            r"async\s+safety)\b"
        ),
        True,
    ),
    (
        "db_migration",
        "Database migration safety",
        "migration_review",
        re.compile(r"(?i)\b(migrat|schema|prisma|alembic|flyway|sql)\b"),
        True,
    ),
    (
        "production_build",
        "Production build + smoke",
        "production_build",
        re.compile(r"(?i)\b(deploy|production|release|ship|vercel|docker)\b"),
        True,
    ),
    (
        "browser_e2e",
        "Browser end-to-end journey",
        "browser_e2e",
        re.compile(
            r"(?i)\b(browser|playwright|cypress|puppeteer|e2e|end-?to-?end|"
            r"user\s+journey|click|qr)\b"
        ),
        True,
    ),
)


_NATIVE_ABILITIES = {
    "nextjs_impl": "implement_web_ui",
    "shared_server_state": "design_service_layer",
    "multi_session_browser": "multi_context_browser_testing",
    "api_integration": "integration_test_writing",
    "responsive_ui": "viewport_inspection",
    "auth_review": "trust_boundary_review",
    "accessibility": "a11y_checklist",
    "concurrency_safety": "race_detector_hooks",
    "db_migration": "migration_impact_notes",
    "production_build": "clean_room_build_gate",
    "browser_e2e": "critical_journey_planning",
}


_GAP_COMPENSATION = {
    "multi_session_browser": (
        "No saved multi-session skill — plan and run two independent browser "
        "contexts before claiming the multiplayer journey works."
    ),
    "browser_e2e": (
        "No browser E2E skill — derive critical-path acceptance steps before "
        "implementation; do not substitute unit tests for journey evidence."
    ),
    "auth_review": (
        "No auth skill — treat client-provided identity as untrusted; validate "
        "authorization on the server; record trust-boundary assumptions."
    ),
    "shared_server_state": (
        "No shared-state skill — define repository/state adapter, concurrency "
        "model, and restart behavior before coding."
    ),
    "concurrency_safety": (
        "No concurrency skill — use race/dynamic analysis hooks; add "
        "idempotency and duplicate-request tests."
    ),
    "production_build": (
        "No deploy skill — include clean install → typecheck → build → start "
        "→ smoke in the completion gate."
    ),
    "accessibility": (
        "No a11y skill — check keyboard nav, contrast, and accessible names "
        "for interactive controls."
    ),
    "responsive_ui": (
        "No responsive-UI skill — inspect phone and desktop viewports; verify "
        "loading/empty/error states."
    ),
}


def requirements_from_intent(intent) -> List[ClassifiedRequirement]:
    """Build classified requirements from a TaskIntent (P0.2/P0.3)."""
    out: List[ClassifiedRequirement] = []
    for i, text in enumerate(getattr(intent, "requested_actions", None) or []):
        if text and str(text).strip():
            out.append(
                ClassifiedRequirement(
                    id=f"req-{i + 1}",
                    text=str(text).strip(),
                    kind="requested_action",
                    polarity="required",
                )
            )
    # Prohibited clauses never feed inference — listed only for audit
    for i, text in enumerate(getattr(intent, "prohibited_actions", None) or []):
        if text and str(text).strip():
            out.append(
                ClassifiedRequirement(
                    id=f"proh-{i + 1}",
                    text=str(text).strip(),
                    kind="prohibited",
                    polarity="prohibited",
                )
            )
    return out


def infer_capabilities(
    requirements: "str | Sequence[ClassifiedRequirement] | None" = None,
    *,
    intent=None,
) -> List[Capability]:
    """
    Infer required capabilities from *classified* requirement clauses only.

    Raw prompt strings are accepted for backward compatibility but are treated
    as a single required clause — callers should prefer TaskIntent / classified
    lists. Observations and prohibited clauses never activate capabilities.
    """
    clauses: List[ClassifiedRequirement] = []
    if intent is not None:
        clauses = [
            c
            for c in requirements_from_intent(intent)
            if c.polarity == "required" and c.kind == "requested_action"
        ]
    elif isinstance(requirements, str):
        # Legacy path: one synthetic clause — still no whole-message secondary scan
        text = (requirements or "").strip()
        if text:
            clauses = [
                ClassifiedRequirement(
                    id="req-1",
                    text=text,
                    kind="requested_action",
                    polarity="required",
                )
            ]
    elif requirements:
        for i, item in enumerate(requirements):
            if isinstance(item, ClassifiedRequirement):
                if item.polarity == "required" and item.kind == "requested_action":
                    clauses.append(item)
            elif isinstance(item, str) and item.strip():
                clauses.append(
                    ClassifiedRequirement(
                        id=f"req-{i + 1}",
                        text=item.strip(),
                        kind="requested_action",
                        polarity="required",
                    )
                )

    # Aggregate activations with multi-requirement provenance
    by_id: dict[str, Capability] = {}
    for clause in clauses:
        span = clause.text
        for cap_id, label, evidence, pattern, critical in _CAPABILITY_RULES:
            m = pattern.search(span)
            if not m:
                continue
            matched = m.group(0)
            reason = "explicit requested verification"
            # Soften implementation-flavored caps when the clause is investigative
            if re.search(
                r"(?i)\b(investigate|diagnose|determine|explain|why|mapping)\b", span
            ):
                reason = "explicit requested investigation/verification"
            existing = by_id.get(cap_id)
            if existing:
                support = tuple(
                    dict.fromkeys(
                        list(existing.supporting_requirement_ids or ())
                        + (existing.requirement_id,)
                        + (clause.id,)
                    )
                )
                by_id[cap_id] = Capability(
                    id=cap_id,
                    label=label,
                    evidence_type=evidence,
                    reason=existing.reason or reason,
                    critical=critical,
                    requirement_id=existing.requirement_id or clause.id,
                    matched_span=existing.matched_span or matched,
                    confidence=max(existing.confidence, 0.88),
                    supporting_requirement_ids=support,
                )
            else:
                by_id[cap_id] = Capability(
                    id=cap_id,
                    label=label,
                    evidence_type=evidence,
                    reason=reason,
                    critical=critical,
                    requirement_id=clause.id,
                    matched_span=matched,
                    confidence=0.88,
                    supporting_requirement_ids=(clause.id,),
                )
    return list(by_id.values())


def build_capability_plan(
    requirements: "str | Sequence[ClassifiedRequirement] | None" = None,
    *,
    skill_capabilities: Sequence[str] = (),
    skill_ids: Sequence[str] = (),
    intent=None,
) -> CapabilityPlan:
    """
    required_capabilities = infer(classified requirements)
    available = skills + native abilities
    coverage_gaps = required - available
    """
    required = infer_capabilities(requirements, intent=intent)
    skill_caps = {c.strip().lower() for c in skill_capabilities if c and c.strip()}
    skill_ids_l = {s.strip().lower() for s in skill_ids if s and s.strip()}

    available_from_skills: List[str] = []
    available_native: List[str] = []
    gaps: List[Capability] = []
    compensation: List[str] = []

    for cap in required:
        native = _NATIVE_ABILITIES.get(cap.id, "")
        if native:
            available_native.append(native)

        covered_by_skill = False
        # Match if any skill capability label overlaps keywords from cap.id/label
        tokens = set(re.split(r"[_\s/-]+", cap.id.lower())) | set(
            re.split(r"[_\s/-]+", cap.label.lower())
        )
        tokens = {t for t in tokens if len(t) > 2}
        for sc in skill_caps:
            sc_tokens = set(re.split(r"[_\s/-]+", sc))
            if tokens & sc_tokens:
                covered_by_skill = True
                available_from_skills.append(sc)
                break
        if not covered_by_skill:
            for sid in skill_ids_l:
                if any(t in sid for t in tokens):
                    covered_by_skill = True
                    available_from_skills.append(sid)
                    break

        if not covered_by_skill:
            gaps.append(cap)
            tip = _GAP_COMPENSATION.get(cap.id)
            if tip:
                compensation.append(tip)
            else:
                compensation.append(
                    f"No saved skill for '{cap.label}' — compensate with an "
                    f"explicit {cap.evidence_type} workflow before completion."
                )

    # Deduplicate lists while preserving order
    def _dedupe(items: List[str]) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for i in items:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    return CapabilityPlan(
        required=required,
        available_from_skills=_dedupe(available_from_skills),
        available_native=_dedupe(available_native),
        coverage_gaps=gaps,
        compensation=_dedupe(compensation),
    )


def format_capability_plan(plan: CapabilityPlan) -> str:
    lines = ["Capability plan (prove completion, not only match skills):", ""]
    if not plan.required:
        lines.append("  (no specialized capabilities inferred)")
        return "\n".join(lines)
    lines.append("Required capabilities:")
    for c in plan.required:
        mark = "✓" if c.id not in {g.id for g in plan.coverage_gaps} else "○"
        crit = " critical" if c.critical else ""
        lines.append(
            f"  {mark} {c.label} [{c.evidence_type}]{crit}"
        )
    if plan.available_from_skills:
        lines.append("")
        lines.append("Covered by saved skills:")
        for s in plan.available_from_skills:
            lines.append(f"  - {s}")
    if plan.coverage_gaps:
        lines.append("")
        lines.append("Coverage gaps (compensate with workflow — do not skip):")
        for tip in plan.compensation:
            lines.append(f"  - {tip}")
    return "\n".join(lines)
