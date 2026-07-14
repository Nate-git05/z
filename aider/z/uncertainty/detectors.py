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
            f"{suggested_fix or 'review and address this uncertainty'}."
        )
    meta = {
        "high_stakes": signals.high_stakes_hit,
        "migration": signals.migration_hit,
        "reference_count": signals.reference_count,
        "blast_radius_threshold": signals.blast_radius_threshold,
        "tests_relevant_exist": signals.tests_relevant_exist,
        "tests_passed": signals.tests_passed,
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
    """Locate tests in the same module or that reference changed symbols by name."""
    root = Path(root)
    found: Set[str] = set()
    changed = [Path(f) for f in files_changed]

    for path in changed:
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

    # Scan test dirs for symbol name references
    symbol_names = [s for s in symbols if s and len(s) > 2]
    test_globs = ["**/test_*.py", "**/*_test.py", "**/*.test.ts", "**/*.spec.ts", "**/tests/**/*.py"]
    for pattern in test_globs:
        for tf in root.glob(pattern):
            if not tf.is_file():
                continue
            try:
                text = tf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Same-file / module name hints
            for path in changed:
                if path.stem and path.stem in text and path.stem not in ("index", "main", "app"):
                    try:
                        found.add(str(tf.relative_to(root)))
                    except ValueError:
                        found.add(str(tf))
                    break
            for sym in symbol_names:
                short = sym.rsplit(".", 1)[-1]
                if short in text:
                    try:
                        found.add(str(tf.relative_to(root)))
                    except ValueError:
                        found.add(str(tf))
                    break

    return sorted(found)


def detect_missing_or_failing_tests(
    signals: DetectionSignals,
    *,
    relevant_tests: Sequence[str],
    tests_passed: Optional[bool],
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
        signals.tests_relevant_exist = False
        nodes.append(
            _make_node(
                title="No relevant tests found for this change",
                node_type=NodeType.MISSING_TEST,
                signals=signals,
                summary="Changed code has no co-located or symbol-referencing tests.",
                explanation=(
                    "After the change, no relevant test suite was found by checking for "
                    "tests in the same file/module or referencing the changed function/class "
                    "by name."
                ),
                why_uncertain="Absence of a checkable test signal for the edited symbols.",
                what_could_go_wrong="Regressions in this code path may ship unnoticed.",
                suggested_fix="Add unit or integration tests covering the changed behavior.",
                suggested_tests=[
                    f"Add tests covering {s}" for s in (signals.symbols_changed[:3] or ["the changed behavior"])
                ],
                suggested_prompt=(
                    "Add tests for the recent change in "
                    f"{', '.join(signals.files_changed[:3])}. Cover the primary success path "
                    "and at least one failure/edge path."
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
                title="Relevant tests failed after this change",
                node_type=NodeType.MISSING_TEST,
                signals=signals,
                summary="Relevant tests exist but failed; do not proceed silently.",
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
                    f"failures in: {', '.join(relevant_tests[:5])}."
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

    if signals.high_stakes_hit and not any(n.type == NodeType.MIGRATION_RISK for n in nodes):
        # High-stakes category is not its own node type — surface as Edge Case with
        # forced Medium+ risk (payment/auth/security/database keyword match).
        node = _make_node(
            title="High-stakes code path changed (payment / auth / security / data)",
            node_type=NodeType.EDGE_CASE,
            signals=signals,
            summary=(
                "Changed paths, imports, or symbols match payment, auth, security, "
                "or database keywords — auto-flagged at least Medium risk."
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
                title=f"Assumed response shape for {api}",
                node_type=NodeType.API_ASSUMPTION,
                signals=sig,
                summary=(
                    f"Code involving {api} was written from trained/assumed knowledge; "
                    "no live call was observed this session."
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
) -> List[UncertaintyNode]:
    nodes: List[UncertaintyNode] = []
    for nf in new_files:
        result = pattern_results.get(nf) or PatternSearchResult()
        if not result.matches:
            signals.pattern_match_found = False
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
                    what_could_go_wrong="The new file may diverge from project conventions and be hard to maintain.",
                    suggested_fix="Align the new file with the closest existing module style, or document why it differs.",
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
            nodes.append(
                _make_node(
                    title=f"Conflicting patterns near {Path(nf).name}",
                    node_type=NodeType.PATTERN_INCONSISTENCY,
                    signals=signals,
                    summary="Multiple conflicting patterns exist for this kind of code.",
                    explanation=(
                        f"Pattern search for {nf} found multiple candidates: "
                        f"{', '.join(result.matches[:8])}."
                    ),
                    why_uncertain="Unclear which existing convention should be followed.",
                    what_could_go_wrong="Inconsistent APIs and duplicated approaches across the codebase.",
                    suggested_fix="Pick one canonical pattern and refactor toward it.",
                    suggested_prompt=(
                        f"Resolve pattern inconsistency for {nf}. Candidates: "
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
            title=f"Shared logic blast radius: {label}",
            node_type=NodeType.SHARED_LOGIC,
            signals=signals,
            summary=(
                f"{reference_count} references/imports exceed the blast-radius threshold "
                f"of {threshold}."
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
# Edge cases (scoped model listing — the one self-report trigger)
# ---------------------------------------------------------------------------


def detect_edge_cases(
    signals: DetectionSignals,
    *,
    edge_cases: Sequence[str],
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    """Each listed edge case considered but not fully handled becomes its own node."""
    nodes: List[UncertaintyNode] = []
    signals.edge_cases_listed = list(edge_cases)
    for case in edge_cases:
        case = (case or "").strip()
        if not case:
            continue
        nodes.append(
            _make_node(
                title=f"Edge case not fully handled: {case[:80]}",
                node_type=NodeType.EDGE_CASE,
                signals=signals,
                summary=f"Considered but not fully handled: {case}",
                explanation=(
                    "After generating the change, the agent listed edge cases considered but "
                    f"not fully handled. This item: {case}"
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
                extra_signals={"edge_case": case},
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# Requirement gaps
# ---------------------------------------------------------------------------


def detect_requirement_gaps(
    signals: DetectionSignals,
    *,
    checklist: TaskChecklist,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
) -> List[UncertaintyNode]:
    nodes: List[UncertaintyNode] = []
    for item in checklist.items:
        if item.status == "Fully Addressed":
            continue
        signals.requirement_gaps.append(item.text)
        nodes.append(
            _make_node(
                title=f"Requirement gap: {item.text[:80]}",
                node_type=NodeType.REQUIREMENT_GAP,
                signals=signals,
                summary=f"Checklist item marked {item.status}.",
                explanation=(
                    f"Asked for: {item.text}\n"
                    f"Delivery status: {item.status}\n"
                    "Compared the stored task checklist against what was actually built."
                ),
                why_uncertain="Sub-requirement was not marked Fully Addressed after implementation.",
                what_could_go_wrong="User intent remains partially unmet; follow-up work will be needed.",
                suggested_fix=f"Complete the requirement: {item.text}",
                suggested_prompt=(
                    f"The requirement '{item.text}' is marked {item.status}. "
                    "Implement the missing pieces and confirm against the original checklist."
                ),
                task_id=task_id or checklist.task_id,
                task_title=task_title or checklist.title,
                created_by_session=created_by_session,
                created_by_user=created_by_user,
                extra_signals={
                    "requirement_id": item.id,
                    "requirement_text": item.text,
                    "requirement_status": item.status,
                },
            )
        )
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
            title="High confidence: matches tested pattern and tests passed",
            node_type=NodeType.HIGH_CONFIDENCE,
            signals=signals,
            summary=(
                "Change closely matches an existing well-tested pattern and relevant tests passed."
            ),
            explanation=(
                "Positive signal for review: pattern match found, relevant tests exist and passed. "
                "Still sorted by risk — high-stakes categories remain visible."
            ),
            why_uncertain="Not uncertain — recorded as an explicit safety signal in the tree.",
            what_could_go_wrong="Residual risk remains if the pattern match was superficial.",
            suggested_fix="Optional spot-check; no mandatory remediation.",
            suggested_prompt=(
                "Optionally spot-check the high-confidence change in "
                f"{', '.join(signals.files_changed[:3])} — tests already passed against a known pattern."
            ),
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            created_by_user=created_by_user,
            status=NodeStatus.OPEN,
        )
    ]


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
