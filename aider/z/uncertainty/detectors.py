"""Concrete, checkable uncertainty detectors.

Each trigger derives from session signals — not model self-rated confidence.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

from .risk import DetectionSignals, collect_base_signals, derive_confidence_tier, derive_risk_tier
from .schema import (
    CONFIG_ENV_PATTERNS,
    DEFAULT_BLAST_RADIUS_THRESHOLD,
    TODO_MARKERS,
    Area,
    NodeStatus,
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
    UncertaintyNode,
    infer_area,
    path_looks_migration,
    text_looks_high_stakes,
    text_looks_migration,
)


def _blast_threshold() -> int:
    raw = os.environ.get("Z_BLAST_RADIUS_THRESHOLD", "")
    try:
        return max(1, int(raw)) if raw else DEFAULT_BLAST_RADIUS_THRESHOLD
    except ValueError:
        return DEFAULT_BLAST_RADIUS_THRESHOLD


def _make_node(
    *,
    title: str,
    node_type: NodeType,
    signals: DetectionSignals,
    summary: str,
    explanation: str = "",
    why_uncertain: str = "",
    what_could_go_wrong: str = "",
    suggested_fix: str = "",
    suggested_tests: Optional[List[str]] = None,
    suggested_prompt: str = "",
    files: Optional[Sequence[str]] = None,
    symbols: Optional[Sequence[str]] = None,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
    extra_signals: Optional[dict] = None,
    status: NodeStatus = NodeStatus.OPEN,
) -> UncertaintyNode:
    files_l = list(files if files is not None else signals.files_changed)
    symbols_l = list(symbols if symbols is not None else signals.symbols_changed)
    conf = derive_confidence_tier(signals, node_type)
    risk = derive_risk_tier(signals, node_type)
    if not suggested_prompt:
        suggested_prompt = (
            f"Regarding '{title}' in {', '.join(files_l[:3]) or 'the recent change'}: "
            f"{suggested_fix or 'review and address this risk'}."
        )
    meta = {
        "high_stakes": signals.high_stakes_hit,
        "migration": signals.migration_hit,
        "reference_count": signals.reference_count,
        "blast_radius_threshold": signals.blast_radius_threshold,
        "tests_relevant_exist": signals.tests_relevant_exist,
        "tests_passed": signals.tests_passed,
        "docs_touched": signals.docs_touched,
        "live_api_verified": signals.live_api_verified,
        "pattern_match_found": signals.pattern_match_found,
        "conflicting_patterns": signals.conflicting_patterns,
        "unverifiable_config_refs": list(signals.unverifiable_config_refs),
        "mcp_unverifiable": signals.mcp_unverifiable,
    }
    if extra_signals:
        meta.update(extra_signals)
    return UncertaintyNode(
        title=title,
        type=node_type,
        confidence_tier=conf,
        risk_tier=risk,
        summary=summary,
        explanation=explanation,
        files_affected=files_l,
        symbols_affected=symbols_l,
        why_uncertain=why_uncertain,
        what_could_go_wrong=what_could_go_wrong,
        suggested_fix=suggested_fix,
        suggested_tests=list(suggested_tests or []),
        suggested_prompt=suggested_prompt,
        status=status,
        area=infer_area(files_l),
        task_id=task_id,
        task_title=task_title,
        created_by_session=created_by_session,
        created_by_user=created_by_user,
        signals=meta,
    )


# ---------------------------------------------------------------------------
# Test relevance
# ---------------------------------------------------------------------------


def find_relevant_tests(
    root: Path,
    files_changed: Sequence[str],
    symbols: Sequence[str] = (),
) -> List[str]:
    """Locate tests in the same module or that reference changed symbols by name.

    Includes nested layouts (e.g. ``tests/.../implementations/test_<stem>.py``)
    that co-location heuristics alone miss.
    """
    root = Path(root)
    found: Set[str] = set()
    changed = [Path(f) for f in files_changed]
    # Skip test files themselves as "changed module" stems when scanning —
    # still allow them to appear if they match another changed production file.
    prod_changed = [
        p
        for p in changed
        if not (
            p.name.startswith("test_")
            or p.name.endswith("_test.py")
            or ".test." in p.name
            or ".spec." in p.name
        )
    ]
    if not prod_changed:
        prod_changed = list(changed)

    for path in prod_changed:
        stem = path.stem
        parent = path.parent
        candidates = [
            parent / f"test_{stem}.py",
            parent / f"{stem}_test.py",
            parent / "tests" / f"test_{stem}.py",
            root / "tests" / f"test_{stem}.py",
            root / "tests" / parent.name / f"test_{stem}.py",
            parent / f"{stem}.test.ts",
            parent / f"{stem}.spec.ts",
            parent / f"{stem}.test.js",
            parent / f"{stem}.spec.js",
        ]
        for c in candidates:
            if c.is_file():
                try:
                    found.add(str(c.relative_to(root)))
                except ValueError:
                    found.add(str(c))
        # Nested / unconventional but conventional-named tests anywhere under root
        if stem and stem not in ("index", "main", "app", "init", "utils", "helpers"):
            for pattern in (
                f"**/test_{stem}.py",
                f"**/{stem}_test.py",
                f"**/{stem}.test.ts",
                f"**/{stem}.spec.ts",
                f"**/{stem}.test.js",
                f"**/{stem}.spec.js",
            ):
                for tf in root.glob(pattern):
                    if not tf.is_file():
                        continue
                    if any(
                        part in ("node_modules", ".git", "venv", ".venv", "dist", "build")
                        for part in tf.parts
                    ):
                        continue
                    try:
                        found.add(str(tf.relative_to(root)))
                    except ValueError:
                        found.add(str(tf))

    # Scan test dirs for symbol / module-stem references
    symbol_names = [s for s in symbols if s and len(s) > 2]
    test_globs = [
        "**/test_*.py",
        "**/*_test.py",
        "**/*.test.ts",
        "**/*.spec.ts",
        "**/tests/**/*.py",
    ]
    scanned = 0
    scan_limit = 800
    for pattern in test_globs:
        for tf in root.glob(pattern):
            if not tf.is_file():
                continue
            if any(
                part in ("node_modules", ".git", "venv", ".venv", "dist", "build")
                for part in tf.parts
            ):
                continue
            scanned += 1
            if scanned > scan_limit:
                break
            try:
                text = tf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            hit = False
            for path in prod_changed:
                if (
                    path.stem
                    and path.stem in text
                    and path.stem not in ("index", "main", "app", "init", "utils", "helpers")
                ):
                    hit = True
                    break
            if not hit:
                for sym in symbol_names:
                    short = sym.rsplit(".", 1)[-1]
                    if len(short) > 2 and short in text:
                        hit = True
                        break
            if hit:
                try:
                    found.add(str(tf.relative_to(root)))
                except ValueError:
                    found.add(str(tf))
        if scanned > scan_limit:
            break

    return sorted(found)


def classify_relevant_tests(
    relevant: Sequence[str],
    edited: Sequence[str] = (),
    *,
    new_files: Sequence[str] = (),
) -> tuple[List[str], List[str]]:
    """
    Split discovered relevant tests into pre-existing vs newly written this turn.

    Newly written tests cannot substitute for running the established ones.
    Prefer ``new_files`` (diff ``new file mode`` / ``new_files_this_turn``).
    When that list is empty, treat nothing as newly written — fail closed so
    every discovered file must still execute.
    """
    new_set = {n.replace("\\", "/") for n in (new_files or ())}
    preexisting: List[str] = []
    newly_written: List[str] = []
    for rel in relevant:
        r = (rel or "").replace("\\", "/")
        if not r:
            continue
        if r in new_set:
            newly_written.append(r)
        else:
            preexisting.append(r)
    return preexisting, newly_written


def detect_missing_or_failing_tests(
    signals: DetectionSignals,
    *,
    relevant_tests: Sequence[str],
    tests_passed: Optional[bool],
    suite_discovered: Optional[int] = None,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """Three outcomes: pass → optional High Confidence; fail → escalate; none → Missing Test."""
    nodes: List[UncertaintyNode] = []
    signals.tests_relevant_exist = bool(relevant_tests)
    signals.tests_passed = tests_passed

    if not relevant_tests:
        # Suite discovery contradicts co-located relevance heuristic — do not false-positive
        if suite_discovered is not None and suite_discovered > 0:
            signals.tests_relevant_exist = True
            if tests_passed is True:
                return []
            if tests_passed is False:
                nodes.append(
                    _make_node(
                        title="Untested path — suite tests failed",
                        node_type=NodeType.MISSING_TEST,
                        signals=signals,
                        summary="The test suite ran and failed; do not treat this as NO_TESTS.",
                        explanation=(
                            f"Discovered {suite_discovered} test(s) at suite level even though "
                            "co-located relevance matching found none. State is TESTS_FAILED."
                        ),
                        why_uncertain="Failing suite is a concrete negative correctness signal.",
                        what_could_go_wrong="Shipping with red tests breaks known contracts.",
                        suggested_fix="Fix failing tests until the suite passes.",
                        suggested_prompt=(
                            "The test suite failed. Trace each failure to its real cause "
                            "(often a test helper/fixture missing a new field). Fix the "
                            "helper or the implementation — do NOT add "
                            "getattr(obj, 'new_param', default) in production code just "
                            "to absorb AttributeError from outdated tests."
                        ),
                        task_id=task_id,
                        task_title=task_title,
                        created_by_session=created_by_session,
                        created_by_user=created_by_user,
                        status=NodeStatus.NEEDS_HUMAN_REVIEW,
                        extra_signals={
                            "suite_discovered": suite_discovered,
                            "verify_state": "TESTS_FAILED",
                        },
                    )
                )
                return nodes
            # Suite discovered but pass/fail unknown — skip "no relevant tests" FP
            return []

        signals.tests_relevant_exist = False
        nodes.append(
            _make_node(
                title="Untested path — no thorough test for this change",
                node_type=NodeType.MISSING_TEST,
                signals=signals,
                summary="I haven’t tested this path thoroughly — no relevant tests found.",
                explanation=(
                    "Like a careful human developer: the change has no co-located or "
                    "symbol-referencing tests, so there is no checkable evidence this path works."
                ),
                why_uncertain="Absence of a checkable test signal for the edited symbols.",
                what_could_go_wrong="Regressions in this code path may ship unnoticed.",
                suggested_fix="Add a focused test for the happy path and one failure/edge case.",
                suggested_tests=[
                    f"Add tests covering {s}"
                    for s in (signals.symbols_changed[:3] or ["the changed behavior"])
                ],
                suggested_prompt=(
                    "Add tests for the recent change in "
                    f"{', '.join(signals.files_changed[:3])}. Cover the primary success path "
                    "and at least one failure/edge path. Run them."
                ),
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                status=NodeStatus.OPEN,
            )
        )
        return nodes

    if tests_passed is False:
        nodes.append(
            _make_node(
                title="Untested path — relevant tests failed",
                node_type=NodeType.MISSING_TEST,
                signals=signals,
                summary="Tests exist but failed; a careful human would not ship this yet.",
                explanation=(
                    "Relevant tests were identified and executed. They failed. "
                    f"Test files: {', '.join(relevant_tests[:8])}."
                ),
                why_uncertain="Failing tests are a concrete negative correctness signal.",
                what_could_go_wrong="Shipping with red tests breaks known contracts.",
                suggested_fix="Fix the failing tests or correct the implementation until they pass.",
                suggested_tests=list(relevant_tests[:5]),
                suggested_prompt=(
                    "The relevant tests failed after the last edit. Inspect and fix the "
                    f"failures in: {', '.join(relevant_tests[:5])}. "
                    "Trace the failure to its actual cause (e.g. a test helper that needs "
                    "the new constructor field). Do not paper over with "
                    "getattr(..., default) in production code."
                ),
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                status=NodeStatus.NEEDS_HUMAN_REVIEW,
                extra_signals={"relevant_tests": list(relevant_tests)},
            )
        )
        return nodes

    # tests exist and passed (or unknown but exist) — High Confidence handled separately
    return nodes


# ---------------------------------------------------------------------------
# High-stakes / migration
# ---------------------------------------------------------------------------


def detect_high_stakes_and_migration(
    signals: DetectionSignals,
    *,
    file_contents: Optional[dict[str, str]] = None,
    migration_data_impact: Optional[str] = None,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    nodes: List[UncertaintyNode] = []
    contents = file_contents or {}

    if signals.migration_hit or any(path_looks_migration(f) for f in signals.files_changed):
        signals.migration_hit = True
        impact = migration_data_impact or (
            "Agent should state what happens to existing data under the new schema "
            "(nulls, defaults, backfill needs). No explicit data-impact statement was provided."
        )
        nodes.append(
            _make_node(
                title="Database schema / migration change",
                node_type=NodeType.MIGRATION_RISK,
                signals=signals,
                summary="A migration or schema change was detected; existing data impact needs review.",
                explanation=(
                    f"Migration-related paths or keywords were found in: "
                    f"{', '.join(f for f in signals.files_changed if path_looks_migration(f) or text_looks_migration(f)) or ', '.join(signals.files_changed[:5])}. "
                    f"Data impact: {impact}"
                ),
                why_uncertain="Schema changes affect live data in ways that are hard to reverse.",
                what_could_go_wrong="Null constraints, missing defaults, or incomplete backfills can break production reads/writes.",
                suggested_fix="Review migration for nullability, defaults, and required backfill; add a dry-run plan.",
                suggested_prompt=(
                    "Review the migration in "
                    f"{', '.join(signals.files_changed[:3])}. Explicitly state what happens to "
                    "existing rows (nulls, defaults, backfill), and add a safe roll-forward/back plan."
                ),
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={"migration_data_impact": impact},
            )
        )

    # Content-based high stakes (imports / function names inside files)
    for _fpath, text in contents.items():
        if text_looks_high_stakes(text) or text_looks_migration(text):
            signals.high_stakes_hit = True
            break

    if signals.high_stakes_hit and not any(
        n.type in (NodeType.MIGRATION_RISK, NodeType.HIGH_STAKES) for n in nodes
    ):
        node = _make_node(
            title="High-stakes surface — money, auth, security, or data loss",
            node_type=NodeType.HIGH_STAKES,
            signals=signals,
            summary=(
                "I’m extra paranoid here: payment/auth/security/data paths were touched."
            ),
            explanation=(
                "Keyword/module pattern match against billing, auth, payment, migration, "
                "security (and related) hit on files or symbols: "
                f"{', '.join(signals.files_changed[:5])}; symbols: "
                f"{', '.join(signals.symbols_changed[:5]) or 'n/a'}."
            ),
            why_uncertain="Category of code is inherently high-stakes regardless of other signals.",
            what_could_go_wrong=(
                "Incorrect auth, payment, or security logic can cause breaches or financial loss."
            ),
            suggested_fix="Add targeted review and tests for the high-stakes paths touched.",
            suggested_prompt=(
                "Re-review the high-stakes changes in "
                f"{', '.join(signals.files_changed[:3])} for auth/payment/security correctness "
                "and add focused tests."
            ),
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
        )
        if node.risk_tier == Tier.LOW:
            node.risk_tier = Tier.MEDIUM
        nodes.append(node)

    return nodes


# ---------------------------------------------------------------------------
# API assumption / MCP
# ---------------------------------------------------------------------------


def detect_api_assumptions(
    signals: DetectionSignals,
    *,
    assumed_apis: Sequence[str],
    live_verified_apis: Set[str],
    mcp_unverifiable: Sequence[str] = (),
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    nodes: List[UncertaintyNode] = []
    unverified = [a for a in assumed_apis if a and a not in live_verified_apis]
    for api in unverified:
        sig = DetectionSignals(**{**signals.__dict__})
        sig.live_api_verified = False
        nodes.append(
            _make_node(
                title=f"Unverified assumption — {api} behavior",
                node_type=NodeType.API_ASSUMPTION,
                signals=sig,
                summary=(
                    f"I’m assuming {api} behaves exactly as I think — no live call this session."
                ),
                explanation=(
                    f"Session tracking shows no executed real call to '{api}' with an observed "
                    "response. The agent may have guessed request/response shapes."
                ),
                why_uncertain="No live-verified call this session for this external API/library.",
                what_could_go_wrong="Field names, status codes, or error shapes may not match production.",
                suggested_fix=f"Execute a real call against {api} (or a sandbox) and align the code to the observed response.",
                suggested_prompt=(
                    f"Verify the {api} integration with a live or recorded response. "
                    "Update types/parsers to match the real shape and add a regression test."
                ),
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={"api": api, "live_verified": False},
            )
        )

    for tool in mcp_unverifiable:
        sig = DetectionSignals(**{**signals.__dict__})
        sig.mcp_unverifiable = True
        sig.live_api_verified = False
        nodes.append(
            _make_node(
                title=f"Unverified MCP result from {tool}",
                node_type=NodeType.API_ASSUMPTION,
                signals=sig,
                summary=f"MCP tool '{tool}' returned data that could not be independently verified.",
                explanation=(
                    "When the agent uses a connected MCP tool and the result is unverified/"
                    "unverifiable, an API Assumption node is generated using the same mechanism "
                    "as assumed external APIs."
                ),
                why_uncertain="MCP result was not cross-checked against an authoritative source this session.",
                what_could_go_wrong="Downstream code may encode incorrect assumptions from the MCP payload.",
                suggested_fix=f"Manually verify the {tool} result and confirm how the code consumes it.",
                suggested_prompt=(
                    f"The MCP tool '{tool}' returned unverifiable data. Confirm the result, "
                    "then update or test the consuming code accordingly."
                ),
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={"mcp_tool": tool, "mcp_unverifiable": True},
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# Pattern inconsistency / new file
# ---------------------------------------------------------------------------


@dataclass
class PatternSearchResult:
    matches: List[str] = field(default_factory=list)
    conflicting: bool = False
    searched_for: str = ""


def detect_pattern_issues(
    signals: DetectionSignals,
    *,
    new_files: Sequence[str],
    pattern_results: dict[str, PatternSearchResult],
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
    emit_new_file_noise: bool = True,
    emit_pattern_misfit: bool = True,
) -> List[UncertaintyNode]:
    """
    Pattern misfit / new-file noise.

    In greenfield repos, callers should set emit_new_file_noise=False —
    "no peers yet" is expected, not a human worry.
    """
    from .context import is_scaffold_file

    nodes: List[UncertaintyNode] = []
    for nf in new_files:
        if is_scaffold_file(nf):
            continue
        result = pattern_results.get(nf) or PatternSearchResult()
        if not result.matches:
            signals.pattern_match_found = False
            if not emit_new_file_noise:
                continue
            nodes.append(
                _make_node(
                    title=f"New file with no pattern match: {Path(nf).name}",
                    node_type=NodeType.NEW_FILE_NO_PATTERN,
                    signals=signals,
                    summary="A new file was added without a clear existing pattern to follow.",
                    explanation=(
                        f"Before writing {nf}, similar existing functions/files/patterns were "
                        "searched. No clear match was found."
                        + (f" Search key: {result.searched_for}." if result.searched_for else "")
                    ),
                    why_uncertain="No established local pattern to validate structure against.",
                    what_could_go_wrong="The new file may diverge from project conventions.",
                    suggested_fix="Align with the closest existing module style, or document why it differs.",
                    suggested_prompt=(
                        f"Review new file {nf}. Find the closest existing pattern in the repo "
                        "and refactor this file to match, or explain the intentional divergence."
                    ),
                    files=[nf],
                    task_id=task_id,
                    task_title=task_title,
                    created_by_session=created_by_session,
                    created_by_user=created_by_user,
                    extra_signals={"pattern_matches": [], "searched_for": result.searched_for},
                )
            )
        elif result.conflicting or len(result.matches) > 1:
            signals.conflicting_patterns = True
            signals.pattern_match_found = True
            if not emit_pattern_misfit and not result.conflicting:
                continue
            if not emit_pattern_misfit:
                # Young repos: only surface hard conflicts
                if not result.conflicting:
                    continue
            nodes.append(
                _make_node(
                    title=f"Pattern misfit near {Path(nf).name}",
                    node_type=NodeType.PATTERN_INCONSISTENCY,
                    signals=signals,
                    summary="I copied a pattern but I’m not sure it fits this context.",
                    explanation=(
                        f"Pattern search for {nf} found multiple candidates: "
                        f"{', '.join(result.matches[:8])}."
                    ),
                    why_uncertain="Unclear which existing convention should be followed.",
                    what_could_go_wrong="Inconsistent APIs and duplicated approaches across the codebase.",
                    suggested_fix="Pick one canonical pattern and refactor toward it.",
                    suggested_prompt=(
                        f"Resolve pattern misfit for {nf}. Candidates: "
                        f"{', '.join(result.matches[:5])}. Choose one style and align the new code."
                    ),
                    files=[nf, *result.matches[:5]],
                    task_id=task_id,
                    task_title=task_title,
                    created_by_session=created_by_session,
                    created_by_user=created_by_user,
                    extra_signals={
                        "pattern_matches": list(result.matches),
                        "conflicting": True,
                    },
                )
            )
        else:
            signals.pattern_match_found = True
    return nodes


def detect_missing_sibling_companions(
    signals: DetectionSignals,
    *,
    root: Path,
    new_files: Sequence[str],
    pattern_results: dict[str, PatternSearchResult],
    diff: str = "",
    files_changed: Optional[Sequence[str]] = None,
    file_contents: Optional[dict[str, str]] = None,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """
    When a new file matches an existing family, check whether siblings share a
    companion trait (registry dict, ``__all__``, package exports, …) that the
    new file's diff did not update.

    Mechanical comparison against files on disk — not framework-specific.
    """
    from .context import is_scaffold_file
    from .sibling_traits import find_sibling_companion_gaps

    nodes: List[UncertaintyNode] = []
    seen_keys: Set[str] = set()

    for nf in new_files:
        nf = (nf or "").replace("\\", "/")
        if not nf or is_scaffold_file(nf):
            continue
        result = pattern_results.get(nf) or PatternSearchResult()
        if not result.matches:
            continue
        gaps = find_sibling_companion_gaps(
            Path(root),
            new_file=nf,
            sibling_matches=result.matches,
            diff=diff or "",
            files_changed=files_changed or signals.files_changed,
            file_contents=file_contents,
        )
        for gap in gaps:
            key = f"{gap.new_file}|{gap.companion_file}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            sib_preview = ", ".join(Path(s).name for s in gap.sibling_files[:5])
            node = _make_node(
                title=(
                    f"Missing sibling registration for {Path(nf).name} "
                    f"in {Path(gap.companion_file).name}"
                ),
                node_type=NodeType.PATTERN_COMPANION_GAP,
                signals=signals,
                summary=(
                    f"Sibling files like {sib_preview} are registered in "
                    f"{gap.companion_file}, but this new file is not."
                ),
                explanation=(
                    f"New file {nf} matches an existing family. "
                    f"{gap.evidence} "
                    "This is a project convention (registry / exports / index), "
                    "not a code import edge — so relevance-based maps often miss it."
                ),
                why_uncertain=(
                    "Peers in this family share a companion registration trait; "
                    "the new file's diff does not update that companion."
                ),
                what_could_go_wrong=(
                    "The feature ships as dead code — correct implementation, but "
                    "never discovered/loaded the way users of this codebase expect."
                ),
                suggested_fix=(
                    f"Register {Path(nf).stem} in {gap.companion_file} the same way "
                    f"siblings do (ids seen for peers: "
                    f"{', '.join(gap.sibling_ids_found[:6])})."
                ),
                suggested_prompt=(
                    f"New file {nf} follows the same pattern as {sib_preview}, but "
                    f"those siblings also appear in {gap.companion_file} and this "
                    f"one does not. Add the matching {gap.trait_kind} entry for "
                    f"{', '.join(gap.missing_ids[:3])} — do not invent a new "
                    "registration style."
                ),
                files=[nf, gap.companion_file, *list(gap.sibling_files)[:4]],
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={
                    "pattern_companion_gap": True,
                    "companion_file": gap.companion_file,
                    "companion_trait": gap.trait_kind,
                    "sibling_files": list(gap.sibling_files),
                    "missing_ids": list(gap.missing_ids),
                },
            )
            node.risk_tier = Tier.MEDIUM
            node.confidence_tier = Tier.LOW
            nodes.append(node)
    return nodes


# ---------------------------------------------------------------------------
# Blast radius
# ---------------------------------------------------------------------------


def detect_blast_radius(
    signals: DetectionSignals,
    *,
    reference_count: int,
    referenced_symbol: str = "",
    referencing_files: Sequence[str] = (),
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    threshold = signals.blast_radius_threshold or _blast_threshold()
    signals.blast_radius_threshold = threshold
    signals.reference_count = reference_count
    if reference_count < threshold:
        return []
    label = referenced_symbol or (signals.symbols_changed[0] if signals.symbols_changed else "changed module")
    return [
        _make_node(
            title=f"Integration ripple — {label} is widely used",
            node_type=NodeType.SHARED_LOGIC,
            signals=signals,
            summary=(
                f"There might be integration effects I haven’t thought about: "
                f"{reference_count} references (threshold {threshold})."
            ),
            explanation=(
                f"After the change, reference/dependency count for '{label}' is {reference_count} "
                f"(threshold={threshold}). "
                + (
                    f"Referencing files include: {', '.join(list(referencing_files)[:10])}."
                    if referencing_files
                    else ""
                )
            ),
            why_uncertain="Changes to widely shared logic can break distant call sites.",
            what_could_go_wrong="Subtle behavioral changes propagate across many modules.",
            suggested_fix="Add characterization tests at major call sites; consider a staged rollout.",
            suggested_prompt=(
                f"'{label}' has {reference_count} references (threshold {threshold}). "
                "Review call sites and add tests for the highest-traffic dependents."
            ),
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
            extra_signals={
                "reference_count": reference_count,
                "blast_radius_threshold": threshold,
                "referenced_symbol": label,
                "referencing_files": list(referencing_files)[:20],
            },
        )
    ]


# ---------------------------------------------------------------------------
# TODO / unclear comments
# ---------------------------------------------------------------------------


def scan_todo_markers(text: str, near_lines: Optional[Sequence[int]] = None) -> List[str]:
    hits: List[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        if near_lines is not None and not any(abs(i - n) <= 15 for n in near_lines):
            continue
        for marker in TODO_MARKERS:
            if marker in line:
                hits.append(f"L{i}: {line.strip()[:120]}")
                break
    return hits


def detect_todo_comments(
    signals: DetectionSignals,
    *,
    todos_by_file: dict[str, List[str]],
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    nodes: List[UncertaintyNode] = []
    for fpath, hits in todos_by_file.items():
        if not hits:
            continue
        signals.todo_markers_near_change = True
        nodes.append(
            _make_node(
                title=f"Pre-existing TODO/FIXME near changes in {Path(fpath).name}",
                node_type=NodeType.TODO_COMMENT,
                signals=signals,
                summary="TODO/FIXME/XXX (or similar) markers sit near the edited code.",
                explanation="Markers found:\n" + "\n".join(hits[:12]),
                why_uncertain="Pre-existing known uncertainty in this area of the code.",
                what_could_go_wrong="The agent may build on unfinished or known-broken assumptions.",
                suggested_fix="Resolve or explicitly acknowledge the nearby TODOs before relying on this path.",
                suggested_prompt=(
                    f"Address the TODO/FIXME markers near the recent edits in {fpath}: "
                    + "; ".join(hits[:3])
                ),
                files=[fpath],
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={"todo_hits": hits[:20]},
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# Unverifiable config
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(
    r"""(?:os\.environ(?:\.get)?\s*\[\s*['"]([A-Z0-9_]+)['"]|"""
    r"""os\.getenv\s*\(\s*['"]([A-Z0-9_]+)['"]|"""
    r"""process\.env\.([A-Z0-9_]+)|"""
    r"""\$\{?([A-Z][A-Z0-9_]+)\}?)"""
)


def extract_config_refs(text: str) -> List[str]:
    refs: List[str] = []
    for pat in CONFIG_ENV_PATTERNS:
        if pat in text:
            refs.append(pat)
    for m in _ENV_VAR_RE.finditer(text):
        name = next((g for g in m.groups() if g), None)
        if name:
            refs.append(name)
    # de-dupe preserving order
    seen = set()
    out = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def detect_unverifiable_config(
    signals: DetectionSignals,
    *,
    config_refs_by_file: dict[str, List[str]],
    accessible_env_keys: Optional[Set[str]] = None,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """Flag env/secrets/infra config the agent cannot inspect or confirm."""
    accessible = accessible_env_keys if accessible_env_keys is not None else set(os.environ.keys())
    nodes: List[UncertaintyNode] = []
    all_unverifiable: List[str] = []
    files: List[str] = []
    for fpath, refs in config_refs_by_file.items():
        missing = []
        for r in refs:
            # Pattern tokens like os.environ aren't env keys
            if r in CONFIG_ENV_PATTERNS or "(" in r:
                # Structural reference to env access — still unverifiable if no concrete key resolved
                if not any(k in accessible for k in refs if k.isupper()):
                    missing.append(r)
                continue
            if r.isupper() and r not in accessible:
                missing.append(r)
        if missing:
            files.append(fpath)
            all_unverifiable.extend(missing)

    if not all_unverifiable:
        return []

    signals.unverifiable_config_refs = sorted(set(all_unverifiable))
    nodes.append(
        _make_node(
            title="Unverifiable production config / secrets referenced",
            node_type=NodeType.UNVERIFIABLE_CONFIG,
            signals=signals,
            summary=(
                "Change references environment variables, secrets, or infrastructure config "
                "the agent cannot inspect."
            ),
            explanation=(
                "This is a structural fact about agent access, not a guess. "
                f"Unverifiable refs: {', '.join(signals.unverifiable_config_refs[:15])}."
            ),
            why_uncertain="Agent has no access to confirm production values for these keys.",
            what_could_go_wrong="Wrong defaults, missing secrets, or mis-named env vars break deploys.",
            suggested_fix="Confirm required env vars and document expected values; add startup validation.",
            suggested_prompt=(
                "The change references unverifiable config "
                f"({', '.join(signals.unverifiable_config_refs[:8])}). "
                "Add validation for required env vars and document expected production values."
            ),
            files=files or signals.files_changed,
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
        )
    )
    return nodes


# ---------------------------------------------------------------------------
# Edge cases — structural first (AST/regex), model list as supplement only
# ---------------------------------------------------------------------------


def detect_edge_cases(
    signals: DetectionSignals,
    *,
    edge_cases: Sequence[str] = (),
    file_contents: Optional[dict[str, str]] = None,
    discussed_text: str = "",
    test_blob: str = "",
    diff: str = "",
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """
    Edge Case Blind Spot from checkable control-flow, not model self-rating.

    1. Enumerate branches in changed files (AST for Python).
    2. Flag undiscussed / untested edge-ish branches (else/except/None/empty…).
    3. Optionally add model-listed cases that aren't already covered — supplement only.
       An empty model list can no longer silence this detector.
    """
    from .edges import (
        collect_branches_from_files,
        parse_changed_lines_from_diff,
        select_undiscussed_branches,
    )

    nodes: List[UncertaintyNode] = []
    model_cases = [c.strip() for c in (edge_cases or []) if c and str(c).strip()]
    signals.edge_cases_listed = list(model_cases)

    # Merge model list into "discussed" so structural won't double-count
    discussed = "\n".join(
        [
            discussed_text or "",
            "\n".join(model_cases),
        ]
    )

    changed_lines = parse_changed_lines_from_diff(diff) if diff else None
    branches = collect_branches_from_files(
        file_contents or {},
        changed_lines_by_file=changed_lines,
    )
    # If diff scoping wiped everything (path mismatch), fall back to full-file scan
    if not branches and file_contents:
        branches = collect_branches_from_files(file_contents, changed_lines_by_file=None)

    undiscussed = select_undiscussed_branches(
        branches,
        discussed_text=discussed,
        test_blob=test_blob or "",
        limit=4,
    )
    structural_labels: List[str] = []
    for br in undiscussed:
        label = br.label()
        structural_labels.append(label)
        nodes.append(
            _make_node(
                title=f"Edge case blind spot: {label[:80]}",
                node_type=NodeType.EDGE_CASE,
                signals=signals,
                summary=(
                    f"This might break on weird data — {br.kind} path at "
                    f"{Path(br.path).name}:{br.lineno} looks unhandled."
                ),
                explanation=(
                    f"Structural control-flow in {br.path}:{br.lineno} "
                    f"({br.kind}): `{br.condition}`. "
                    "Flagged because this branch was not discussed in the agent reply "
                    "and no relevant test mentions the enclosing symbol. "
                    "Independent of any model self-reported edge-case list."
                ),
                why_uncertain=(
                    "A checkable branch exists in the change without evidence it was "
                    "considered or tested."
                ),
                what_could_go_wrong=(
                    f"Hitting the {br.kind} path ({br.condition[:120]}) may mis-handle input."
                ),
                suggested_fix=(
                    f"Handle or explicitly document the {br.kind} path in "
                    f"{br.enclosing or br.path}, and add a test."
                ),
                suggested_tests=[
                    f"Test {br.enclosing or Path(br.path).stem} covering: {br.condition[:80]}"
                ],
                suggested_prompt=(
                    f"Fully handle this edge path in {br.path}:{br.lineno} "
                    f"({br.kind}: {br.condition}). Add a test that would have failed before."
                ),
                files=[br.path],
                symbols=[br.enclosing] if br.enclosing else None,
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={
                    "edge_case": label,
                    "edge_source": "structural",
                    "branch_kind": br.kind,
                    "branch_line": br.lineno,
                },
            )
        )

    # Model list is supplemental — only add cases not already covered structurally
    struct_blob = " ".join(structural_labels).lower()
    for case in model_cases:
        case_l = case.lower()
        # Skip if structural already covers similar wording
        tokens = [t for t in re.findall(r"[a-z0-9_]{4,}", case_l)]
        if tokens and any(t in struct_blob for t in tokens):
            continue
        nodes.append(
            _make_node(
                title=f"Edge case blind spot: {case[:80]}",
                node_type=NodeType.EDGE_CASE,
                signals=signals,
                summary=f"This might break on weird data — not fully handled: {case}",
                explanation=(
                    "Agent self-reported this edge case as considered but not fully handled. "
                    f"Item: {case}. Treated as a supplement to structural branch detection."
                ),
                why_uncertain="Explicitly acknowledged incomplete handling of this edge case.",
                what_could_go_wrong=f"Encountering this case in production: {case}",
                suggested_fix=f"Implement handling and tests for: {case}",
                suggested_tests=[f"Test covering edge case: {case}"],
                suggested_prompt=(
                    f"Fully handle this edge case in the recent change: {case}. "
                    "Add a test that would have failed before the fix."
                ),
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={"edge_case": case, "edge_source": "model"},
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# Requirement gaps
# ---------------------------------------------------------------------------


_TEST_RUN_LANG = re.compile(
    r"(?i)\b(run|execute)\b.{0,40}\b(test|tests|suite|pytest|unittest)\b"
    r"|\b(test|tests|suite)\b.{0,20}\b(pass|passed|green)\b"
    r"|\brun\s+the\s+tests?\b"
)
_PROCESS_FINISH_LANG = re.compile(
    r"(?i)\b(fix\s+failures?|do\s+not\s+commit|don't\s+commit|"
    r"before\s+finish|working\s+tree|until\s+.{0,40}pass)\b"
)
_CONCURRENCY_LANG = re.compile(
    r"(?i)\b(concurren|thread[- ]?safe|race|contention|prune)\b"
)


def reconcile_requirement_with_signals(
    item: RequirementItem,
    signals: DetectionSignals,
    *,
    relevant_tests: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """
    Cross-check checklist status against concrete session signals.

    Returns a corrected status when signals contradict an open gap, else None.
    Kinds with an explicit absence-of-verifier are never silently cleared —
    Unverifiable stays Unverifiable until a real check is registered.
    """
    from .evidence_strategy import verifier_for

    text = item.text or ""
    kind = (getattr(item, "kind", None) or "product").lower()
    v = verifier_for(kind)
    if not v.has_verifier:
        # Exhaustive registry: no check → never reconcile to Fully/Partial
        return None

    if signals.tests_passed is True:
        if kind == "verification" or _TEST_RUN_LANG.search(text):
            return "Fully Addressed"
        if kind == "process" and _PROCESS_FINISH_LANG.search(text):
            return "Fully Addressed"
        if kind == "process" and re.search(r"(?i)\b(verif|commit\s+gate|uncertainty)\b", text):
            return "Fully Addressed"

    # Documentation: concrete docs_touched (README*/CHANGELOG*/docs/** edited)
    if kind == "documentation":
        if signals.docs_touched is True:
            return "Fully Addressed"
        if signals.docs_touched is False:
            # Pre-existing README must not silently clear a docs requirement
            return "Not Addressed"

    # quality: never Fully via reconcile alone — needs file+symbol+test ledger bar

    return None


def detect_requirement_gaps(
    signals: DetectionSignals,
    *,
    checklist: TaskChecklist,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
    gap_details: Optional[Sequence[dict]] = None,
    relevant_tests: Optional[Sequence[str]] = None,
) -> List[UncertaintyNode]:
    nodes: List[UncertaintyNode] = []
    detail_by_id = {d.get("id"): d for d in (gap_details or []) if d.get("id")}

    # Circuit breaker: chronically unresolved detector → downgrade severity
    noisy = False
    try:
        from .outcomes import detector_circuit_open

        noisy = detector_circuit_open(NodeType.REQUIREMENT_GAP.value)
    except Exception:
        noisy = False

    from .evidence_strategy import hard_block_kind

    for item in checklist.items:
        reconciled = reconcile_requirement_with_signals(
            item, signals, relevant_tests=relevant_tests
        )
        if reconciled:
            item.status = reconciled
        if item.status == "Fully Addressed":
            continue

        kind = getattr(item, "kind", None) or "product"
        signals.requirement_gaps.append(item.text)
        detail = detail_by_id.get(item.id) or {}
        missing = detail.get("missing") or f"Complete: {item.text}"
        evidence = detail.get("evidence") or []
        unverifiable = item.status == "Unverifiable"
        if unverifiable:
            summary = (
                f"Unverifiable — no check exists for this category yet (kind={kind})."
            )
            why = (
                "This requirement kind has an explicit absence-of-verifier in the "
                "registry. We are not claiming it passed — we honestly don't know."
            )
            what_wrong = (
                "Treating an unchecked category as done can hide real gaps; "
                "this flag is informational until a trusted verifier ships."
            )
            prompt_extra = (
                "Do not invent product work to clear this. A verifier for this "
                "kind is not registered yet — leave as Unverifiable / ask the human."
            )
        else:
            summary = f"We didn’t finish what was asked — marked {item.status}."
            why = (
                f"Missing evidence: {missing}"
                if missing
                else "Sub-requirement was not marked Fully Addressed after implementation."
            )
            what_wrong = "User intent remains partially unmet; follow-up work will be needed."
            if kind in ("process", "verification", "decision"):
                prompt_extra = (
                    "This is a process/tooling requirement — do not add product "
                    "commands; satisfy it via verification/session evidence only."
                )
            elif kind == "documentation":
                prompt_extra = (
                    "Update documentation only — do not invent product features."
                )
            elif kind == "investigation":
                prompt_extra = (
                    "This is a named investigative obligation. Open/grep the "
                    "named symbols/paths and either fix a real issue there OR "
                    "explicitly rule it out with session evidence. Do not only "
                    "extend an unrelated prior fix pattern."
                )
            else:
                prompt_extra = "Implement only that gap, then stop."
        node = _make_node(
            title=(
                f"Unverifiable requirement: {item.text[:80]}"
                if unverifiable
                else f"Requirement gap: {item.text[:80]}"
            ),
            node_type=NodeType.REQUIREMENT_GAP,
            signals=signals,
            summary=summary,
            explanation=(
                f"Asked for: {item.text}\n"
                f"Kind: {kind}\n"
                f"Delivery status: {item.status}\n"
                f"Missing: {missing}\n"
                f"Evidence: {', '.join(evidence) if evidence else '(none)'}\n"
                + (
                    "Registry maps this kind to an absence-of-verifier marker."
                    if unverifiable
                    else (
                        "Compared the structured checklist against bound evidence "
                        "(code for product; session/verify for process; README for docs)."
                    )
                )
            ),
            why_uncertain=why,
            what_could_go_wrong=what_wrong,
            suggested_fix=missing,
            suggested_prompt=(
                f"The requirement '{item.text}' is marked {item.status}. "
                f"Missing: {missing}. {prompt_extra}"
            ),
            task_id=task_id or checklist.task_id,
            task_title=task_title or checklist.title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
            extra_signals={
                "requirement_id": item.id,
                "requirement_text": item.text,
                "requirement_status": item.status,
                "requirement_kind": kind,
                "missing": missing,
                "evidence": list(evidence),
                "detector_noisy": noisy,
                "unverifiable": unverifiable,
            },
        )
        # Unverifiable is always Low/informational (rollout: avoid boy-who-cried-wolf)
        if unverifiable or noisy:
            node.risk_tier = Tier.LOW
        elif item.status == "Not Addressed" and hard_block_kind(kind):
            node.risk_tier = Tier.HIGH
        elif kind in ("process", "decision", "verification"):
            node.risk_tier = Tier.LOW
            node.status = NodeStatus.OPEN
        elif kind in ("documentation", "quality"):
            node.risk_tier = Tier.MEDIUM
        else:
            node.risk_tier = Tier.MEDIUM
        if noisy:
            node.summary = (
                node.summary
                + " [detector noise circuit: Requirement Gap has ~0% historical resolution]"
            )
        nodes.append(node)
    return nodes


# ---------------------------------------------------------------------------
# High confidence (positive signal)
# ---------------------------------------------------------------------------


def detect_high_confidence(
    signals: DetectionSignals,
    *,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """Positive signal when change matches a tested pattern and relevant tests pass."""
    if not (
        signals.closely_matches_tested_pattern
        and signals.tests_relevant_exist is True
        and signals.tests_passed is True
    ):
        return []
    return [
        _make_node(
            title="Evidence of safety — tested pattern match",
            node_type=NodeType.HIGH_CONFIDENCE,
            signals=signals,
            summary=(
                "This one I’m actually comfortable with: matches a tested pattern and tests passed."
            ),
            explanation=(
                "Positive signal for review: pattern match found, relevant tests exist and passed. "
                "Still sorted by risk — high-stakes categories remain visible."
            ),
            why_uncertain="Not uncertain — recorded as an explicit safety signal in the tree.",
            what_could_go_wrong="Residual risk remains if the pattern match was superficial.",
            suggested_fix="Optional spot-check; no mandatory remediation.",
            suggested_prompt=(
                "Optionally spot-check the evidence-of-safety change in "
                f"{', '.join(signals.files_changed[:3])} — tests already passed against a known pattern."
            ),
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
            status=NodeStatus.OPEN,
        )
    ]


# ---------------------------------------------------------------------------
# Fragile logic (looks clever / brittle)
# ---------------------------------------------------------------------------

_BROAD_EXCEPT_RE = re.compile(r"except\s*(:|\s+Exception\s*:|\s+BaseException\s*:)")
_NESTED_IF_RE = re.compile(r"(?m)^(?:    ){3,}if\s+")


def detect_fragile_logic(
    signals: DetectionSignals,
    *,
    file_contents: dict[str, str],
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """
    Heuristic 'this feels fragile' detector — dense nesting, broad excepts, magic.
    Skips scaffold files.
    """
    from .context import is_scaffold_file

    nodes: List[UncertaintyNode] = []
    for fpath, text in (file_contents or {}).items():
        if is_scaffold_file(fpath) or not text:
            continue
        reasons = []
        if len(_BROAD_EXCEPT_RE.findall(text)) >= 1:
            reasons.append("broad except / swallow-all error handling")
        if len(_NESTED_IF_RE.findall(text)) >= 3:
            reasons.append("deeply nested conditionals")
        if re.search(r"\bmagic\b|\bhack\b|\btemporary\b", text, re.I):
            reasons.append("self-labeled hack/temporary logic")
        # Many numeric literals in conditionals
        if len(re.findall(r"\bif\s+.*\b\d{2,}\b", text)) >= 3:
            reasons.append("many magic-number conditionals")
        if not reasons:
            continue
        nodes.append(
            _make_node(
                title=f"Fragile logic in {Path(fpath).name}",
                node_type=NodeType.FRAGILE_LOGIC,
                signals=signals,
                summary="The logic looks correct but feels fragile or clever.",
                explanation=(
                    f"Heuristics in {fpath}: {', '.join(reasons)}. "
                    "A careful human would want characterization tests before relying on this."
                ),
                why_uncertain="Complexity / brittle patterns raise gut-feel uncertainty.",
                what_could_go_wrong="Small input changes may break nested or swallowed error paths.",
                suggested_fix="Simplify or add characterization tests around the fragile block.",
                suggested_prompt=(
                    f"Review fragile logic in {fpath} ({', '.join(reasons)}). "
                    "Simplify if possible and add a characterization test."
                ),
                files=[fpath],
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={"fragile_reasons": reasons},
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# Failure blind spot (I/O without failure handling)
# ---------------------------------------------------------------------------

_IO_CALL_RE = re.compile(
    r"(?i)\b("
    r"requests\.(get|post|put|patch|delete)|httpx\.|fetch\(|"
    r"open\(|pathlib\.Path\([^\)]*\)\.write|"
    r"\.execute\(|session\.(get|post|commit)|"
    r"subprocess\.|urlopen\("
    r")\b"
)
_ERROR_HANDLE_RE = re.compile(r"(?i)\b(except|try:|raises?|timeout|retry|rollback)\b")


def detect_failure_blind_spots(
    signals: DetectionSignals,
    *,
    file_contents: dict[str, str],
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """Flag I/O-ish code that lacks nearby failure handling."""
    from .context import is_scaffold_file

    nodes: List[UncertaintyNode] = []
    for fpath, text in (file_contents or {}).items():
        if is_scaffold_file(fpath) or not text:
            continue
        io_hits = _IO_CALL_RE.findall(text)
        if not io_hits:
            continue
        if _ERROR_HANDLE_RE.search(text):
            continue
        nodes.append(
            _make_node(
                title=f"Failure blind spot in {Path(fpath).name}",
                node_type=NodeType.FAILURE_BLIND_SPOT,
                signals=signals,
                summary="I didn’t check what happens if this fails — I/O without error handling.",
                explanation=(
                    f"{fpath} performs external/I/O calls ({len(io_hits)} hit(s)) but has no "
                    "nearby try/except, timeout, retry, or rollback signals."
                ),
                why_uncertain="Failure modes for I/O were not evidenced in the change.",
                what_could_go_wrong="Network/disk/DB failures may crash or corrupt state silently.",
                suggested_fix="Handle failure paths and add a test that exercises one failure.",
                suggested_prompt=(
                    f"Add failure handling for I/O in {fpath} and a test for at least one "
                    "failure path (timeout, non-200, or write error)."
                ),
                files=[fpath],
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={"io_hits": len(io_hits)},
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# Absorbed failure — broad except near new external import/call
# ---------------------------------------------------------------------------

_IMPORT_OR_EXTERNAL_RE = re.compile(
    r"(?mi)^\s*(?:import\s+\w+|from\s+\w+\s+import)\b|"
    r"\b(?:requests\.|httpx\.|subprocess\.|urlopen\(|"
    r"pip\s+install|__import__\(|importlib\.)"
)


def detect_absorbed_failures(
    signals: DetectionSignals,
    *,
    file_contents: dict[str, str],
    diff: str = "",
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """
    Broad except Exception / bare except near a new import or external call.

    This is the limp-forward pattern behind dependency fabrication — unexpected
    failures get swallowed into a generic path instead of surfacing.
    """
    from .context import is_scaffold_file
    from .edges import parse_changed_lines_from_diff

    nodes: List[UncertaintyNode] = []
    changed = parse_changed_lines_from_diff(diff) if diff else {}

    for fpath, text in (file_contents or {}).items():
        if is_scaffold_file(fpath) or not text:
            continue
        lines = text.splitlines()
        changed_set = set(changed.get(fpath.replace("\\", "/"), []) or [])
        # Also try basename-relative keys
        if not changed_set:
            for k, v in changed.items():
                if k.endswith(Path(fpath).name) or Path(k).name == Path(fpath).name:
                    changed_set = set(v or [])
                    break

        broad_lines = []
        external_lines = []
        for i, line in enumerate(lines, start=1):
            if _BROAD_EXCEPT_RE.search(line):
                broad_lines.append(i)
            if _IMPORT_OR_EXTERNAL_RE.search(line) or _IO_CALL_RE.search(line):
                external_lines.append(i)

        if not broad_lines or not external_lines:
            continue

        # Prefer proximity on changed lines; fall back to same-file co-presence
        # when the broad except or the external call was introduced in this diff.
        paired = False
        reason_bits = []
        for bl in broad_lines:
            for el in external_lines:
                if abs(bl - el) <= 40:
                    introduced = (not changed_set) or (bl in changed_set) or (el in changed_set)
                    if introduced:
                        paired = True
                        reason_bits.append(f"except@L{bl} near external@L{el}")
                        break
            if paired:
                break
        if not paired:
            continue

        node = _make_node(
            title=f"Absorbed failure in {Path(fpath).name}",
            node_type=NodeType.ABSORBED_FAILURE,
            signals=signals,
            summary=(
                "A broad except may be swallowing unexpected failures near a new "
                "import or external call — limp-forward instead of fail-loud."
            ),
            explanation=(
                f"{fpath}: {'; '.join(reason_bits)}. "
                "Catch only expected exception types; let unexpected errors surface "
                "so install/import/environment failures cannot be papered over."
            ),
            why_uncertain=(
                "Broad exception handlers hide the real failure mode from the human "
                "and from the verify gate."
            ),
            what_could_go_wrong=(
                "Missing dependencies, API errors, or corrupt state get converted into "
                "generic exits/fallbacks — same instinct as fabricating a stub package."
            ),
            suggested_fix=(
                "Replace bare/Exception catch with specific types, or re-raise "
                "unexpected errors after logging."
            ),
            suggested_prompt=(
                f"In {fpath}, narrow the broad except near the new import/external call. "
                "Do not swallow ModuleNotFoundError / ImportError / unexpected failures."
            ),
            files=[fpath],
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
            status=NodeStatus.NEEDS_HUMAN_REVIEW,
            extra_signals={
                "absorbed_failure": True,
                "broad_except_lines": broad_lines[:8],
                "external_lines": external_lines[:8],
            },
        )
        node.risk_tier = Tier.HIGH
        node.confidence_tier = Tier.LOW
        nodes.append(node)
    return nodes


# ---------------------------------------------------------------------------
# Unvalidated config / constructor parameters
# ---------------------------------------------------------------------------

_INIT_DEF_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)def\s+__init__\s*\((?P<args>[^)]*)\)\s*:"
)
_NUMERIC_PARAM_RE = re.compile(
    r"\b(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*(?::\s*(?:int|float|bool))?\s*(?:=\s*[^,)\n]+)?"
)
_VALIDATE_NEAR_RE = re.compile(
    r"(?i)\b(raise\s+|ValueError|TypeError|AssertionError|assert\s+|if\s+not\s+"
    r"|if\s+\w+\s*(?:<|>|<=|>=|==|!=)|validate|clamp|bounds?|range\()\b"
)
_CONFIGISH_NAMES = re.compile(
    r"(?i)\b(timeout|retries?|limit|max_|min_|ttl|threshold|tolerance|"
    r"capacity|size|count|port|rate|window|batch|workers?|concurrency)\w*\b"
)


def detect_unvalidated_config(
    signals: DetectionSignals,
    *,
    file_contents: dict[str, str],
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """
    Flag constructors that take numeric/config-like parameters with no nearby
    validation (Codex #8 — fail immediately on bad config).
    """
    from .context import is_scaffold_file

    nodes: List[UncertaintyNode] = []
    for fpath, text in (file_contents or {}).items():
        if is_scaffold_file(fpath) or not text:
            continue
        if not fpath.endswith((".py",)):
            continue
        for m in _INIT_DEF_RE.finditer(text):
            args = m.group("args") or ""
            # Skip self-only
            params = [
                p.strip()
                for p in args.split(",")
                if p.strip() and p.strip() not in ("self", "cls", "*", "**")
            ]
            configish = [
                p
                for p in params
                if _CONFIGISH_NAMES.search(p)
                or re.search(r":\s*(int|float)\b", p)
            ]
            if not configish:
                continue
            # Body: from __init__ to next def at same indent (approx 40 lines)
            start = m.end()
            body = text[start : start + 1200]
            if _VALIDATE_NEAR_RE.search(body):
                continue
            names = []
            for p in configish:
                nm = re.match(r"\*?(\w+)", p.strip().lstrip("*"))
                if nm:
                    names.append(nm.group(1))
            node = _make_node(
                title=f"Unvalidated config in {Path(fpath).name}.__init__",
                node_type=NodeType.UNVALIDATED_CONFIG,
                signals=signals,
                summary=(
                    "Constructor accepts numeric/config parameters with no visible "
                    "validation — invalid values can limp into runtime."
                ),
                explanation=(
                    f"{fpath} __init__ parameters look config-like "
                    f"({', '.join(names[:6])}) but the constructor body has no "
                    "raise/assert/bounds check nearby."
                ),
                why_uncertain="Bad config should fail at construction, not later.",
                what_could_go_wrong=(
                    "Negative timeouts, NaN tolerances, or out-of-range limits "
                    "propagate until a distant failure."
                ),
                suggested_fix=(
                    "Validate each public config input in __init__ (type/range) and "
                    "add a unit test for at least one invalid value."
                ),
                suggested_prompt=(
                    f"In {fpath}, validate __init__ config params "
                    f"({', '.join(names[:6])}) and reject invalid values immediately."
                ),
                files=[fpath],
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={
                    "unvalidated_params": names[:10],
                    "constructor_config": True,
                },
            )
            nodes.append(node)
            break  # one node per file
    return nodes


# ---------------------------------------------------------------------------
# Failure-absorption taxonomy scanner (named shapes; data-extensible)
# ---------------------------------------------------------------------------

_GETATTR_DEFAULT_RE = re.compile(
    r"""getattr\s*\(\s*[^,\n]+,\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*,"""
)
_NEW_PARAM_IN_DIFF_RE = re.compile(
    r"(?m)^\+"  # added line in unified diff
    r".*?(?:"
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:bool|int|float|str|Optional|None)|"  # typed field
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:True|False|None|\d+|['\"])|"  # defaulted kw
    r"add_argument\s*\(\s*['\"]--([A-Za-z0-9-]+)['\"]|"  # argparse
    r"['\"]--([A-Za-z0-9-]+)['\"]"  # click/typer style
    r")"
)


def _normalize_param_name(name: str) -> str:
    return (name or "").strip().lstrip("-").replace("-", "_").lower()


def _new_params_from_diff(diff: str) -> Set[str]:
    """Collect constructor / argparse parameter names introduced in this diff."""
    names: Set[str] = set()
    if not diff:
        return names
    for m in _NEW_PARAM_IN_DIFF_RE.finditer(diff):
        for g in m.groups():
            if g:
                names.add(_normalize_param_name(g))
    # Also pull __init__ signature additions more carefully
    for m in re.finditer(r"(?m)^\+.*def\s+__init__\s*\(([^)]*)\)", diff):
        args = m.group(1) or ""
        for part in args.split(","):
            part = part.strip()
            if not part or part in ("self", "cls", "*", "**"):
                continue
            if part.startswith("*"):
                part = part.lstrip("*")
            name = re.split(r"[:\=]", part, maxsplit=1)[0].strip()
            if name.isidentifier():
                names.add(_normalize_param_name(name))
    return {
        n
        for n in names
        if n
        and len(n) > 1
        and n
        not in {
            "self",
            "cls",
            "true",
            "false",
            "none",
            "optional",
            "bool",
            "int",
            "float",
            "str",
            "return",
            "args",
            "kwargs",
        }
    }


def _tier_from_severity(severity: str) -> Tier:
    s = (severity or "Medium").strip().lower()
    if s == "high":
        return Tier.HIGH
    if s == "low":
        return Tier.LOW
    return Tier.MEDIUM


def detect_failure_absorption(
    signals: DetectionSignals,
    *,
    file_contents: dict[str, str],
    diff: str = "",
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """
    Scan the diff against the named failure-absorption taxonomy.

    Adding a new absorption shape is a data row in ABSORPTION_TAXONOMY — not a
    new ad-hoc detector function. Trusted patterns (hard_block=True) may
    High-block; newer rows ship as Low/Medium informational until promoted.
    """
    from .absorption_taxonomy import ABSORPTION_TAXONOMY, scan_failure_absorption
    from .context import is_scaffold_file

    nodes: List[UncertaintyNode] = []
    new_params = _new_params_from_diff(diff)
    pattern_by_id = {p.pattern_id: p for p in ABSORPTION_TAXONOMY}

    # --- getattr_new_param_default: refine with new-params cross-check --------
    # Still uses file_contents so we catch getattr even when the helper line
    # moved outside the smallest diff hunk; only fires for params this diff added.
    if new_params:
        for fpath, text in (file_contents or {}).items():
            if is_scaffold_file(fpath) or not text:
                continue
            rel = fpath.replace("\\", "/")
            if any(p in rel for p in ("/tests/", "test_", "_test.py", "conftest.py")):
                continue
            hits = []
            for m in _GETATTR_DEFAULT_RE.finditer(text):
                attr = _normalize_param_name(m.group(1))
                if attr in new_params:
                    hits.append(attr)
            if not hits:
                continue
            uniq = sorted(set(hits))
            pat = pattern_by_id.get("getattr_new_param_default")
            node = _make_node(
                title=f"Permissive getattr for newly added param in {Path(fpath).name}",
                node_type=NodeType.GETATTR_SHORTCUT,
                signals=signals,
                summary=(
                    "Production code uses getattr(..., default) for a parameter "
                    "this same diff just introduced — likely papering over a red test."
                ),
                explanation=(
                    f"{fpath}: getattr fallback for {', '.join(uniq)}. "
                    "Those names appear as new constructor/CLI parameters in the diff. "
                    "Fix the outdated test helper/fixture instead of weakening the contract. "
                    f"[taxonomy:{pat.pattern_id if pat else 'getattr_new_param_default'}]"
                ),
                why_uncertain=(
                    "A permissive default hides AttributeError from callers that should "
                    "provide the new field (often a hand-built test args() namespace)."
                ),
                what_could_go_wrong=(
                    "The real bug (stale test helper / incomplete call site) stays; "
                    "production silently accepts missing fields forever."
                ),
                suggested_fix=(
                    "Remove the getattr default; update call sites and test helpers to "
                    "pass the new parameter explicitly."
                ),
                suggested_prompt=(
                    f"In {fpath}, remove getattr(..., default) for newly added "
                    f"param(s) {', '.join(uniq)}. Update the test helper / call sites "
                    "to include the field — do not absorb AttributeError in production."
                ),
                files=[fpath],
                task_id=task_id,
                task_title=task_title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                status=NodeStatus.NEEDS_HUMAN_REVIEW,
                extra_signals={
                    "getattr_shortcut": True,
                    "failure_absorption": True,
                    "absorption_pattern_id": "getattr_new_param_default",
                    "absorption_hard_block": True,
                    "getattr_attrs": uniq,
                    "new_params_in_diff": sorted(new_params)[:20],
                },
            )
            node.risk_tier = Tier.HIGH
            node.confidence_tier = Tier.LOW
            nodes.append(node)

    # --- Remaining taxonomy rows: general diff scanner -----------------------
    tax_hits = scan_failure_absorption(
        diff or "",
        pattern_ids=[
            p.pattern_id
            for p in ABSORPTION_TAXONOMY
            if p.pattern_id != "getattr_new_param_default"
        ],
    )
    seen_keys: Set[str] = set()
    for hit in tax_hits:
        key = f"{hit.pattern_id}:{hit.evidence}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        pat = pattern_by_id.get(hit.pattern_id)
        hard = bool(pat.hard_block) if pat else False
        tier = _tier_from_severity(hit.severity)
        node = _make_node(
            title=f"{hit.title}: {hit.line[:60]}",
            node_type=NodeType.FAILURE_ABSORPTION,
            signals=signals,
            summary=(
                f"Failure-absorption pattern '{hit.pattern_id}' in the diff — "
                f"{hit.description}"
            ),
            explanation=(
                f"Taxonomy hit `{hit.pattern_id}`: {hit.evidence}\n"
                f"{hit.description}\n"
                "Adding new absorption shapes is a taxonomy data change, not a "
                "new detector function."
            ),
            why_uncertain=hit.description,
            what_could_go_wrong=(
                "Failures get absorbed into a silent default instead of being fixed "
                "or reported — the same instinct as permissive getattr shortcuts."
            ),
            suggested_fix=(
                "Remove the silent absorption; surface the error or wire the value "
                "through explicitly."
            ),
            suggested_prompt=(
                f"The diff matches failure-absorption pattern '{hit.pattern_id}' "
                f"({hit.evidence}). Remove the silent fallback; fix the real cause."
            ),
            files=list(signals.files_changed[:5]),
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
            status=(
                NodeStatus.NEEDS_HUMAN_REVIEW if hard else NodeStatus.OPEN
            ),
            extra_signals={
                "failure_absorption": True,
                "absorption_pattern_id": hit.pattern_id,
                "absorption_hard_block": hard,
                "absorption_evidence": hit.evidence,
            },
        )
        node.risk_tier = tier
        node.confidence_tier = Tier.LOW
        nodes.append(node)

    return nodes


def detect_getattr_shortcuts(
    signals: DetectionSignals,
    *,
    file_contents: dict[str, str],
    diff: str = "",
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """Back-compat wrapper — getattr row of the failure-absorption taxonomy."""
    nodes = detect_failure_absorption(
        signals,
        file_contents=file_contents,
        diff=diff,
        task_id=task_id,
        task_title=task_title,
        created_by_session=created_by_session,
        created_by_user=created_by_user,
    )
    return [n for n in nodes if n.type == NodeType.GETATTR_SHORTCUT]


def detect_established_solution_gaps(
    signals: DetectionSignals,
    *,
    diff: str = "",
    plan: Optional[object] = None,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """
    Flag diffs that invent a custom solution for a well-known problem
    (IP/email/URL/date/UUID, heaps, caches, concurrency) without evidence the
    plan considered the established approach — or without using the standard
    in the diff itself.
    """
    from .established_solutions import (
        plan_allows_custom_invention,
        scan_invention_in_diff,
    )

    hits = scan_invention_in_diff(diff or "")
    if not hits:
        return []

    considerations = []
    if plan is not None:
        considerations = list(getattr(plan, "established_solutions", None) or [])

    nodes: List[UncertaintyNode] = []
    for hit in hits:
        # Only an explicit custom justification suppresses invention.
        # decision=use_standard without a stdlib import still flags.
        if plan_allows_custom_invention(considerations, hit.category_id):
            continue
        prefer = "; ".join(hit.standard_names[:3])
        node = _make_node(
            title=f"Reinvented established solution: {hit.title}",
            node_type=NodeType.ESTABLISHED_SOLUTION_GAP,
            signals=signals,
            summary=(
                f"The diff appears to solve '{hit.title}' from scratch instead of "
                f"using an established approach ({hit.standard_names[0] if hit.standard_names else 'stdlib'})."
            ),
            explanation=(
                f"{hit.description}\n"
                f"Evidence in diff: {hit.evidence}\n"
                f"Prefer: {prefer}\n"
                "A test suite written by the same process that invented the custom "
                "logic often shares its blind spots — use the standard, or record "
                "an explicit custom justification in the gated plan."
            ),
            why_uncertain=(
                "No plan evidence named the standard approach / justified custom, "
                "and the diff does not import or call the established solution."
            ),
            what_could_go_wrong=(
                "Subtle correctness bugs (legal inputs rejected, illegal inputs "
                "accepted) that hand-rolled tests never cover."
            ),
            suggested_fix=(
                f"Replace the custom logic with: {prefer}. "
                "If a custom implementation is truly required, update the plan "
                f"with decision=custom and a justification for [{hit.category_id}]."
            ),
            suggested_prompt=(
                f"Category [{hit.category_id}] ({hit.title}): the diff invents a "
                f"custom solution ({hit.evidence[:120]}). Switch to "
                f"{hit.standard_names[0] if hit.standard_names else 'the stdlib'} "
                "unless there is an explicit, recorded justification for custom."
            ),
            files=list(signals.files_changed[:5]),
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
            extra_signals={
                "established_solution_gap": True,
                "established_category_id": hit.category_id,
                "established_hard_block": hit.hard_block,
                "established_evidence": hit.evidence,
            },
        )
        sev = (hit.severity or "Medium").strip().lower()
        node.risk_tier = (
            Tier.HIGH if sev == "high" else Tier.LOW if sev == "low" else Tier.MEDIUM
        )
        node.confidence_tier = Tier.LOW
        nodes.append(node)
    return nodes


# ---------------------------------------------------------------------------
# Dependency fabrication / import shadowing
# ---------------------------------------------------------------------------


def detect_dependency_fabrication(
    signals: DetectionSignals,
    *,
    root: Path,
    files_changed: Optional[Sequence[str]] = None,
    execution_log: str = "",
    verification_excerpt: str = "",
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """
    Flag new top-level packages that shadow a declared or recently-missing
    third-party dependency (e.g. freezegun/__init__.py no-op stand-in).
    """
    try:
        from aider.z.deps import (
            collect_declared_dependencies,
            extract_missing_modules,
            extract_pip_install_targets,
            scan_paths_for_fabrication,
        )
    except Exception:
        return []

    root = Path(root)
    paths = list(files_changed if files_changed is not None else signals.files_changed)
    blob = f"{execution_log or ''}\n{verification_excerpt or ''}"
    missing = extract_missing_modules(blob) | extract_pip_install_targets(blob)
    try:
        declared = collect_declared_dependencies(root)
    except Exception:
        declared = set()

    hits = scan_paths_for_fabrication(
        paths,
        root=root,
        missing_modules=missing,
        declared=declared,
    )
    if not hits:
        return []

    nodes: List[UncertaintyNode] = []
    for hit in hits:
        pkg = hit["package"]
        reason = hit["reason"]
        fpath = hit["path"]
        node = _make_node(
            title=f"Dependency fabrication — local '{pkg}' shadows a real package",
            node_type=NodeType.DEPENDENCY_FABRICATION,
            signals=signals,
            summary=(
                f"A new top-level '{pkg}' was added that can shadow the real "
                "third-party library instead of installing it."
            ),
            explanation=(
                f"{reason}\n"
                f"Path: {fpath}\n"
                "This is environment tampering: a no-op or stub package at the "
                "repo root silently replaces the real dependency for the whole "
                "test suite. Install the real package or remove the local stand-in."
            ),
            why_uncertain=(
                "The agent may have fabricated a fake dependency after an import "
                "failure rather than installing the real one."
            ),
            what_could_go_wrong=(
                "Existing tests that rely on real library behavior can pass or fail "
                "for the wrong reason; the safety net is quietly swapped out."
            ),
            suggested_fix=(
                f"Delete the local '{pkg}' stand-in, install the real dependency "
                "from the project requirements / PyPI, and re-run the suite."
            ),
            suggested_prompt=(
                f"Do NOT keep a local '{pkg}' package. Remove {fpath} (and the "
                f"'{pkg}/' tree if present), install the real '{pkg}' dependency, "
                "and re-run tests. Stop if install fails — report the error verbatim."
            ),
            files=[fpath],
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
            status=NodeStatus.NEEDS_HUMAN_REVIEW,
            extra_signals={
                "dependency_fabrication": True,
                "fabricated_package": pkg,
                "fabrication_reason": reason,
                "non_forceable_without_ack": True,
            },
        )
        # Force High — never downgrade via noise circuit
        node.risk_tier = Tier.HIGH
        node.confidence_tier = Tier.LOW
        nodes.append(node)
    return nodes


def count_symbol_references(root: Path, symbol: str, exclude_files: Sequence[str] = ()) -> tuple[int, List[str]]:
    """Simple reference/dependency count via text search for the symbol name."""
    if not symbol or len(symbol) < 2:
        return 0, []
    root = Path(root)
    short = symbol.rsplit(".", 1)[-1]
    exclude = {Path(f).as_posix() for f in exclude_files}
    hits: List[str] = []
    count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".go",
            ".rs",
            ".java",
            ".rb",
        }:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel in exclude:
            continue
        if any(part.startswith(".") or part in ("node_modules", "venv", ".git", "__pycache__") for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        n = text.count(short)
        if n:
            count += n
            hits.append(rel)
    return count, hits[:50]
