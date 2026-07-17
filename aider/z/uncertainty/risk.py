"""
Independent risk and confidence tier derivation from concrete signals.

Never self-rated by the model as a numeric score — tiers come from checkable facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .schema import (
    NodeType,
    Tier,
    path_looks_high_stakes,
    path_looks_migration,
    text_looks_high_stakes,
)


@dataclass
class DetectionSignals:
    """Concrete, checkable facts collected during / after a change."""

    files_changed: List[str] = field(default_factory=list)
    symbols_changed: List[str] = field(default_factory=list)
    high_stakes_hit: bool = False
    migration_hit: bool = False
    tests_relevant_exist: Optional[bool] = None  # None = unknown
    tests_passed: Optional[bool] = None
    # Mechanical: files_changed includes README*/CHANGELOG*/docs/**
    docs_touched: Optional[bool] = None
    live_api_verified: Optional[bool] = None  # None = N/A, False = assumed
    pattern_match_found: Optional[bool] = None
    conflicting_patterns: bool = False
    reference_count: int = 0
    blast_radius_threshold: int = 5
    todo_markers_near_change: bool = False
    unverifiable_config_refs: List[str] = field(default_factory=list)
    edge_cases_listed: List[str] = field(default_factory=list)
    requirement_gaps: List[str] = field(default_factory=list)
    mcp_unverifiable: bool = False
    closely_matches_tested_pattern: bool = False
    # Diff touches atomics/mutexes/threads/volatile/… (see concurrency_checks)
    concurrency_relevant: Optional[bool] = None
    race_detector_ran: Optional[bool] = None
    race_detector_outcome: Optional[str] = None
    # Broader dynamic-risk taxonomy (concurrency / memory_safety / leaks)
    dynamic_risk_relevant: Optional[bool] = None
    dynamic_risk_categories: List[str] = field(default_factory=list)
    sanitizer_ran: Optional[bool] = None
    sanitizer_outcome: Optional[str] = None
    memory_safety_relevant: Optional[bool] = None
    leak_relevant: Optional[bool] = None


def _max_tier(*tiers: Tier) -> Tier:
    order = {Tier.LOW: 0, Tier.MEDIUM: 1, Tier.HIGH: 2}
    return max(tiers, key=lambda t: order[t])


def _min_conf(*tiers: Tier) -> Tier:
    order = {Tier.HIGH: 2, Tier.MEDIUM: 1, Tier.LOW: 0}
    return min(tiers, key=lambda t: order[t])


def derive_risk_tier(signals: DetectionSignals, node_type: NodeType) -> Tier:
    """
    Risk = how bad if wrong. Independent of confidence.
    Category alone can force Medium/High (payments, auth, migrations).
    """
    risk = Tier.LOW

    if signals.high_stakes_hit or signals.migration_hit:
        risk = _max_tier(risk, Tier.MEDIUM)

    if node_type in (NodeType.MIGRATION_RISK, NodeType.HIGH_STAKES):
        risk = _max_tier(risk, Tier.MEDIUM)

    if node_type in (
        NodeType.SHARED_LOGIC,
        NodeType.UNVERIFIABLE_CONFIG,
        NodeType.API_ASSUMPTION,
        NodeType.FAILURE_BLIND_SPOT,
        NodeType.FRAGILE_LOGIC,
    ):
        if signals.high_stakes_hit:
            risk = _max_tier(risk, Tier.HIGH)
        else:
            risk = _max_tier(risk, Tier.MEDIUM)

    if signals.reference_count >= signals.blast_radius_threshold * 2:
        risk = _max_tier(risk, Tier.HIGH)
    elif signals.reference_count >= signals.blast_radius_threshold:
        risk = _max_tier(risk, Tier.MEDIUM)

    if signals.tests_relevant_exist is True and signals.tests_passed is False:
        risk = _max_tier(risk, Tier.HIGH)

    if signals.tests_relevant_exist is False and node_type == NodeType.MISSING_TEST:
        risk = _max_tier(risk, Tier.MEDIUM)

    if node_type == NodeType.REQUIREMENT_GAP:
        risk = _max_tier(risk, Tier.MEDIUM)

    if node_type == NodeType.DEPENDENCY_FABRICATION:
        # Never less than High — local stubs that shadow real deps
        return Tier.HIGH

    if node_type == NodeType.ABSORBED_FAILURE:
        return Tier.HIGH

    if node_type == NodeType.WEAK_TEST:
        return Tier.HIGH

    if node_type == NodeType.UNVALIDATED_CONFIG:
        risk = _max_tier(risk, Tier.MEDIUM)

    if node_type == NodeType.GETATTR_SHORTCUT:
        return Tier.HIGH

    # Default Medium for taxonomy hits; detector overrides risk_tier per pattern.
    if node_type == NodeType.FAILURE_ABSORPTION:
        return Tier.MEDIUM

    if node_type == NodeType.PATTERN_COMPANION_GAP:
        return Tier.MEDIUM

    if node_type == NodeType.ESTABLISHED_SOLUTION_GAP:
        return Tier.MEDIUM

    _dynamic_types = (
        NodeType.CONCURRENCY_RACE,
        NodeType.MEMORY_SAFETY,
        NodeType.LEAK_ANALYSIS,
        NodeType.DYNAMIC_ANALYSIS,
    )
    if node_type in _dynamic_types:
        # Remaining issues / no improvement are serious; tool-missing is reviewable.
        # Clean dynamic runs stay Low risk (informational) but never High confidence.
        outcome = (
            signals.sanitizer_outcome
            or signals.race_detector_outcome
        )
        if outcome in ("no_improvement", "regression"):
            return Tier.HIGH
        if outcome == "clean":
            return Tier.LOW
        return Tier.MEDIUM

    if node_type == NodeType.HIGH_CONFIDENCE:
        if signals.high_stakes_hit or signals.migration_hit:
            risk = _max_tier(risk, Tier.MEDIUM)
        else:
            risk = Tier.LOW

    for f in signals.files_changed:
        if path_looks_high_stakes(f):
            risk = _max_tier(risk, Tier.MEDIUM)
        if path_looks_migration(f):
            risk = _max_tier(risk, Tier.MEDIUM)

    return risk


def derive_confidence_tier(signals: DetectionSignals, node_type: NodeType) -> Tier:
    """
    Confidence = how sure about correctness of what was done.
    Derived from tests, live verification, pattern match — not model self-score.
    """
    if node_type == NodeType.HIGH_CONFIDENCE:
        return Tier.HIGH

    conf = Tier.MEDIUM

    if signals.tests_relevant_exist is True and signals.tests_passed is True:
        conf = Tier.HIGH
    elif signals.tests_relevant_exist is True and signals.tests_passed is False:
        conf = Tier.LOW
    elif signals.tests_relevant_exist is False:
        conf = Tier.LOW

    if signals.live_api_verified is False or signals.mcp_unverifiable:
        conf = _min_conf(conf, Tier.LOW)

    if signals.pattern_match_found is False or signals.conflicting_patterns:
        conf = _min_conf(conf, Tier.LOW)

    if signals.unverifiable_config_refs:
        conf = _min_conf(conf, Tier.LOW)

    if signals.todo_markers_near_change:
        conf = _min_conf(conf, Tier.MEDIUM)

    if signals.closely_matches_tested_pattern and signals.tests_passed is True:
        conf = Tier.HIGH

    if node_type == NodeType.EDGE_CASE:
        conf = _min_conf(conf, Tier.MEDIUM)

    if node_type == NodeType.REQUIREMENT_GAP:
        conf = _min_conf(conf, Tier.LOW)

    # Dynamic analysis never upgrades confidence to High — these bugs are
    # non-deterministic; a clean run is reduced confidence, not proof.
    _dynamic_types = (
        NodeType.CONCURRENCY_RACE,
        NodeType.MEMORY_SAFETY,
        NodeType.LEAK_ANALYSIS,
        NodeType.DYNAMIC_ANALYSIS,
    )
    if (
        node_type in _dynamic_types
        or signals.race_detector_ran
        or signals.sanitizer_ran
        or signals.dynamic_risk_relevant
    ):
        conf = _min_conf(conf, Tier.MEDIUM)
        outcome = signals.sanitizer_outcome or signals.race_detector_outcome
        if outcome in (
            "after_only",
            "tool_missing",
            "reduced",
        ):
            conf = _min_conf(conf, Tier.LOW)

    return conf


def scan_high_stakes(files: Sequence[str], symbols: Sequence[str] = ()) -> bool:
    for f in files:
        if path_looks_high_stakes(f) or path_looks_migration(f):
            return True
        if text_looks_high_stakes(f):
            return True
    for s in symbols:
        if text_looks_high_stakes(s):
            return True
    return False


def collect_base_signals(
    files_changed: Sequence[str],
    symbols_changed: Sequence[str] = (),
    *,
    blast_radius_threshold: int = 5,
) -> DetectionSignals:
    from .checklist import files_touch_docs

    files = list(files_changed)
    symbols = list(symbols_changed)
    return DetectionSignals(
        files_changed=files,
        symbols_changed=symbols,
        high_stakes_hit=scan_high_stakes(files, symbols),
        migration_hit=any(path_looks_migration(f) for f in files),
        docs_touched=files_touch_docs(files),
        blast_radius_threshold=blast_radius_threshold,
    )
