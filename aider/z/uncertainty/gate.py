"""
Tiered verify-before-commit gate for Z.

High risk  → hard block (resolved only, or explicit force override)
Medium risk → soft block requiring explicit user acknowledgment
Low risk   → informational only; never blocks

Verification must actually run tests this session. Zero discovered tests
is a High-risk failure, not a pass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Set

from .risk import DetectionSignals, derive_confidence_tier
from .schema import NodeStatus, NodeType, Tier, UncertaintyNode
from .store import UncertaintyStore
from .verify import VerificationRecord, VerifyState, gate_enabled, verify_edits

# Max reflect loops for generate-tests / fix-tests before hard-blocking.
MAX_TEST_GEN_ATTEMPTS = 1
MAX_TEST_FIX_ATTEMPTS = 2

# Medium types that can soft-block. Pattern/scaffold noise and Evidence of Safety
# are excluded — they stay informational.
_MEDIUM_GATE_TYPES = {
    NodeType.REQUIREMENT_GAP,
    NodeType.UNVERIFIABLE_CONFIG,
    NodeType.API_ASSUMPTION,
    NodeType.MIGRATION_RISK,
    NodeType.HIGH_STAKES,
    NodeType.EDGE_CASE,
    NodeType.TODO_COMMENT,
    NodeType.SHARED_LOGIC,
    NodeType.MISSING_TEST,
    NodeType.FAILURE_BLIND_SPOT,
    NodeType.FRAGILE_LOGIC,
}


@dataclass
class GateResult:
    allow_commit: bool
    reflect_message: Optional[str] = None
    verification: Optional[VerificationRecord] = None
    blocked_high: List[UncertaintyNode] = field(default_factory=list)
    needs_ack_medium: List[UncertaintyNode] = field(default_factory=list)
    acknowledged_medium: List[UncertaintyNode] = field(default_factory=list)
    force_override: bool = False
    reason: str = ""
    claimed_complete: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _force_requested(coder) -> bool:
    if getattr(coder, "force_commit", False):
        return True
    return os.environ.get("Z_FORCE_COMMIT", "").strip() in ("1", "true", "yes")


def _mark_gate_signal(node: UncertaintyNode, kind: str, *, commit_hash: Optional[str] = None):
    node.signals["gate_accepted"] = True
    node.signals["gate_accepted_kind"] = kind
    node.signals["gate_accepted_at"] = _now()
    if commit_hash:
        node.signals["gate_accepted_commit"] = commit_hash


def record_acceptances(
    store: UncertaintyStore,
    nodes: Sequence[UncertaintyNode],
    kind: str,
    *,
    commit_hash: Optional[str] = None,
) -> None:
    try:
        from .outcomes import record_outcome
    except Exception:
        record_outcome = None  # type: ignore

    for node in nodes:
        _mark_gate_signal(node, kind, commit_hash=commit_hash)
        store.nodes[node.id] = node
        if record_outcome and kind in ("force_override", "medium_ack"):
            try:
                record_outcome(
                    node.type,
                    kind,
                    repo_key=getattr(store, "repo_key", "") or "",
                    node_id=node.id,
                )
            except Exception:
                pass
    store.save_local()


def _upsert_verification_node(
    store: UncertaintyStore,
    *,
    title: str,
    summary: str,
    explanation: str,
    files: Sequence[str],
    record: VerificationRecord,
    status: NodeStatus = NodeStatus.NEEDS_HUMAN_REVIEW,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
) -> UncertaintyNode:
    """Create or refresh a High-risk Missing Test verification node."""
    signals = DetectionSignals(
        files_changed=list(files),
        tests_relevant_exist=False,
        tests_passed=False,
    )
    # Prefer matching an open verification node to avoid spam
    for existing in store.list(include_resolved=False):
        if (
            existing.type == NodeType.MISSING_TEST
            and existing.signals.get("verification_blocked")
            and existing.title == title
        ):
            existing.summary = summary
            existing.explanation = explanation
            existing.risk_tier = Tier.HIGH
            existing.confidence_tier = Tier.LOW
            existing.status = status
            existing.files_affected = list(files)
            existing.signals.update(
                {
                    "verification_blocked": True,
                    "verification": record.to_dict(),
                    "tests_passed": False,
                    "tests_relevant_exist": False,
                }
            )
            store.save_local()
            return existing

    node = UncertaintyNode(
        title=title,
        type=NodeType.MISSING_TEST,
        confidence_tier=derive_confidence_tier(signals, NodeType.MISSING_TEST),
        risk_tier=Tier.HIGH,
        summary=summary,
        explanation=explanation,
        files_affected=list(files),
        why_uncertain="No checkable test execution record with discovered tests.",
        what_could_go_wrong="Shipping without verified tests can hide regressions.",
        suggested_fix="Add and run real tests covering the changed behavior until they pass.",
        suggested_prompt=(
            "Verification gate blocked commit: write runnable tests for "
            f"{', '.join(list(files)[:5]) or 'the recent change'}, then run the suite."
        ),
        status=status,
        task_id=task_id,
        task_title=task_title,
        signals={
            "verification_blocked": True,
            "verification": record.to_dict(),
            "tests_passed": False,
            "tests_relevant_exist": False,
            "high_stakes": False,
        },
    )
    # Force High regardless of default derivation
    node.risk_tier = Tier.HIGH
    store.add(node)
    return node


def _effective_gate_tier(node: UncertaintyNode) -> Tier:
    """
    Map a node to the gate tier.

    - Verification / failing-test / Not Addressed requirements → High
    - Explicit High risk_tier → High
    - Actionable Medium types → Medium
    - Everything else → Low (no block)
    """
    if node.signals.get("verification_blocked"):
        return Tier.HIGH
    if node.signals.get("tests_passed") is False and node.type == NodeType.MISSING_TEST:
        return Tier.HIGH
    if node.type == NodeType.REQUIREMENT_GAP:
        req_status = node.signals.get("requirement_status") or ""
        req_kind = (node.signals.get("requirement_kind") or "product").lower()
        # Process/decision/verification gaps must not hard-block commits
        if req_kind in ("process", "decision", "verification"):
            return Tier.LOW
        # Noise circuit: chronically unresolved detector — never hard-block
        if node.signals.get("detector_noisy"):
            return Tier.LOW
        # Documentation gaps are reviewable, not commit-blockers by default
        if req_kind == "documentation":
            return Tier.LOW if req_status != "Not Addressed" else Tier.MEDIUM
        if req_status == "Not Addressed":
            return Tier.HIGH
        if req_status == "Partially Addressed":
            return Tier.MEDIUM
    if node.risk_tier == Tier.HIGH:
        return Tier.HIGH
    if node.risk_tier == Tier.MEDIUM and node.type in _MEDIUM_GATE_TYPES:
        return Tier.MEDIUM
    return Tier.LOW


def _is_resolved_for_high(node: UncertaintyNode) -> bool:
    """High blockers require genuine resolve — Ignored does not clear them."""
    return node.status == NodeStatus.RESOLVED


def _already_acked(node: UncertaintyNode) -> bool:
    return bool(node.signals.get("gate_accepted")) and node.signals.get(
        "gate_accepted_kind"
    ) in ("medium_ack", "force_override")


def classify_nodes(
    nodes: Sequence[UncertaintyNode],
) -> tuple[List[UncertaintyNode], List[UncertaintyNode]]:
    high: List[UncertaintyNode] = []
    medium: List[UncertaintyNode] = []
    for node in nodes:
        if node.status == NodeStatus.RESOLVED:
            continue
        tier = _effective_gate_tier(node)
        if tier == Tier.HIGH:
            if not _is_resolved_for_high(node):
                high.append(node)
        elif tier == Tier.MEDIUM:
            if node.status == NodeStatus.IGNORED:
                continue  # ignore allowed for medium
            if _already_acked(node):
                continue
            medium.append(node)
    return high, medium


def _format_node_lines(nodes: Sequence[UncertaintyNode], limit: int = 8) -> str:
    lines = []
    for node in nodes[:limit]:
        # Show the gate-effective tier so UI matches "N high-risk issue(s)"
        tier = _effective_gate_tier(node)
        lines.append(
            f"  - [{tier.value}] {node.type.value}: {node.title}"
        )
    if len(nodes) > limit:
        lines.append(f"  … and {len(nodes) - limit} more")
    return "\n".join(lines)


def _reflect_generate_tests(edited: Sequence[str], relevant: Sequence[str]) -> str:
    files = ", ".join(list(edited)[:6]) or "the recent change"
    return (
        "Z verification gate: no runnable tests cover this change "
        f"({files}).\n"
        "Write a focused automated test for the new/changed behavior "
        "(pytest or the project's test runner). The test must actually "
        "execute in the suite — creating a file that is never run does "
        "not satisfy verification.\n"
        "Do not claim the task is complete and do not skip tests."
    )


def _reflect_fix_tests(record: VerificationRecord, edited: Sequence[str]) -> str:
    excerpt = (record.output_excerpt or record.error or "")[-1500:]
    files = ", ".join(list(edited)[:6])
    return (
        "Z verification gate: the test suite failed after your edits"
        f"{f' to {files}' if files else ''}.\n"
        f"Command: {record.command}\n"
        f"Exit code: {record.exit_code}\n"
        f"Discovered tests: {record.tests_discovered}\n"
        f"Output (excerpt):\n{excerpt}\n\n"
        "Fix the implementation or tests, then the suite will be re-run. "
        "Do not claim completion while tests are red."
    )


def prepare_commit(coder, edited: Sequence[str]) -> GateResult:
    """
    Run real verification + uncertainty analysis + tiered commit policy.

    May return reflect_message to loop the agent (generate/fix tests).
    """
    if not gate_enabled() or not getattr(coder, "verify_commit_gate", True):
        return GateResult(allow_commit=True, reason="gate disabled")

    engine = getattr(coder, "uncertainty_engine", None)
    store = getattr(coder, "uncertainty_store", None)
    if not engine or not store:
        return GateResult(allow_commit=True, reason="no uncertainty engine")

    root = Path(getattr(coder, "root", None) or os.getcwd())
    edited_list = [str(e) for e in edited]
    io = coder.io

    # --- 1) Real verification ---
    record, relevant = verify_edits(
        root,
        edited_list,
        test_cmd=getattr(coder, "test_cmd", None),
        verbose=bool(getattr(coder, "verbose", False)),
        error_print=io.tool_error,
    )
    coder.last_verification = record
    coder.test_outcome = bool(record.meaningful_pass)
    try:
        engine.ctx.last_verification = record
        engine.record_execution(
            f"verification state={getattr(record.state, 'value', record.state)} "
            f"discovered={record.tests_discovered} exit={record.exit_code} "
            f"cmd={record.command}"
        )
    except Exception:
        pass

    gen_attempts = int(getattr(coder, "_z_verify_gen_attempts", 0) or 0)
    fix_attempts = int(getattr(coder, "_z_verify_fix_attempts", 0) or 0)
    force = _force_requested(coder)

    # Branch on structured VerifyState / suite discovery — NOT on empty
    # find_relevant_tests(). "2 failed, 7 passed" must never become "no tests".
    state = record.state or VerifyState.NOT_RUN
    discovered = record.tests_discovered
    needs_generate = (
        state in (VerifyState.NO_TESTS, VerifyState.RUNNER_MISSING, VerifyState.NOT_RUN)
        or (not record.ran)
        or record.zero_tests
        or (discovered is not None and discovered == 0)
    )
    needs_fix = (
        state in (VerifyState.TESTS_FAILED, VerifyState.COLLECTION_FAILED)
        or (
            record.ran
            and not record.meaningful_pass
            and (discovered or 0) > 0
        )
    )
    # Prefer fix when we know tests existed and failed
    if needs_fix and (discovered or 0) > 0:
        needs_generate = False

    if needs_generate and not record.meaningful_pass:
        node = _upsert_verification_node(
            store,
            title="Untested Path — cannot verify",
            summary="Commit blocked: no discovered tests for this change.",
            explanation=(
                f"Verification state: {state.value}. "
                "Empty/misconfigured suites do not count as success. "
                f"Command: {record.command or '(none)'}. "
                f"Discovered: {discovered}. "
                f"Error: {record.error or record.output_excerpt[-800:] or 'n/a'}."
            ),
            files=edited_list,
            record=record,
            task_id=getattr(engine.ctx, "current_task_id", None),
            task_title=getattr(engine.ctx, "current_task_title", None),
        )
        coder._z_gate_hold_dirty = True
        if not force and gen_attempts < MAX_TEST_GEN_ATTEMPTS:
            coder._z_verify_gen_attempts = gen_attempts + 1
            io.tool_warning(
                f"Verification: {state.value} — generating tests before commit."
            )
            return GateResult(
                allow_commit=False,
                reflect_message=_reflect_generate_tests(edited_list, relevant),
                verification=record,
                blocked_high=[node],
                reason="missing tests — reflect to generate",
            )

    elif needs_fix and not record.meaningful_pass:
        # Suite ran with discovered tests but failed (or collection/smoke failed)
        node = _upsert_verification_node(
            store,
            title="Untested Path — tests failed, commit blocked",
            summary="Commit blocked: test suite did not meaningfully pass.",
            explanation=(
                f"State: {state.value}\n"
                f"Command: {record.command}\n"
                f"Exit: {record.exit_code}\n"
                f"Discovered: {discovered} "
                f"(passed={record.tests_passed} failed={record.tests_failed})\n"
                f"Zero tests: {record.zero_tests}\n"
                f"Smoke: ran={record.smoke_ran} ok={record.smoke_ok} "
                f"({record.smoke_detail})\n"
                f"{record.output_excerpt[-1200:]}"
            ),
            files=edited_list,
            record=record,
            task_id=getattr(engine.ctx, "current_task_id", None),
            task_title=getattr(engine.ctx, "current_task_title", None),
        )
        coder._z_gate_hold_dirty = True
        if not force and fix_attempts < MAX_TEST_FIX_ATTEMPTS:
            coder._z_verify_fix_attempts = fix_attempts + 1
            io.tool_warning(
                f"Verification: {state.value} "
                f"({record.tests_failed or '?'} failed / "
                f"{discovered or '?'} discovered) — attempting fix before commit."
            )
            return GateResult(
                allow_commit=False,
                reflect_message=_reflect_fix_tests(record, edited_list),
                verification=record,
                blocked_high=[node],
                reason="tests failed — reflect to fix",
            )

    else:
        # Meaningful pass — clear prior verification_blocked nodes for these files
        for existing in list(store.list(include_resolved=False)):
            if existing.signals.get("verification_blocked"):
                store.update_status(existing.id, NodeStatus.RESOLVED)

    # --- 2) Human-worry detectors + structured checklist rescore ---
    try:
        content = getattr(coder, "partial_response_content", None) or ""
        ingest = getattr(coder, "_ingest_uncertainty_self_reports", None)
        if callable(ingest):
            ingest(content)
        rels = []
        for path in edited_list:
            try:
                rels.append(coder.get_rel_fname(path))
            except Exception:
                rels.append(str(path))
        new_nodes = engine.analyze_edits(rels, tests_passed=coder.test_outcome)
        if new_nodes:
            try:
                from .ui import print_summary_line

                print_summary_line(io, new_nodes)
            except Exception:
                pass
    except Exception as err:  # noqa: BLE001
        if getattr(coder, "verbose", False):
            io.tool_warning(f"Uncertainty analysis skipped: {err}")

    # --- 3) Auto-act (OFF by default — prevents scope-expanding remediations) ---
    # Enable only with Z_UNCERTAINTY_AUTO_ACT=1. Even then, only narrow prompts.
    auto_act_on = os.environ.get("Z_UNCERTAINTY_AUTO_ACT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if auto_act_on and not force and record.meaningful_pass:
        try:
            from .auto_act import plan_auto_act

            auto_attempts = int(getattr(coder, "_z_auto_act_attempts", 0) or 0)
            act = plan_auto_act(
                store,
                store.list(include_resolved=False),
                attempts=auto_attempts,
                max_attempts=1,
            )
            if act.reflect_message:
                coder._z_auto_act_attempts = auto_attempts + 1
                coder._z_gate_hold_dirty = True
                io.tool_warning(
                    "Uncertainty auto-act (enabled): addressing high-priority worries."
                )
                return GateResult(
                    allow_commit=False,
                    reflect_message=act.reflect_message,
                    verification=record,
                    blocked_high=list(act.acted_on),
                    reason="auto-act on high human worries",
                )
        except Exception as err:  # noqa: BLE001
            if getattr(coder, "verbose", False):
                io.tool_warning(f"Auto-act skipped: {err}")

    # --- 4) Tiered gate policy ---
    open_nodes = store.list(include_resolved=False)
    high, medium = classify_nodes(open_nodes)

    # Ensure verification failure always surfaces as high even if analysis missed it
    if not record.meaningful_pass:
        if not any(n.signals.get("verification_blocked") for n in high):
            high.append(
                _upsert_verification_node(
                    store,
                    title="Untested Path — cannot verify",
                    summary="Commit blocked: verification did not meaningfully pass.",
                    explanation=record.error or record.output_excerpt or "verification failed",
                    files=edited_list,
                    record=record,
                )
            )

    # force already computed above (skip reflect when set)

    if high:
        subject = _format_node_lines(high)
        io.tool_error(
            f"Commit blocked: {len(high)} high-risk issue(s) unresolved.\n{subject}\n"
            "High-risk nodes must be fixed and verified (Ignored does not clear them)."
        )
        if force:
            io.tool_warning(
                "FORCE COMMIT: bypassing high-risk block (--force-commit / Z_FORCE_COMMIT). "
                "Override will be logged on the nodes."
            )
            record_acceptances(store, high, "force_override")
            # Still require medium ack unless force also clears? Force bypasses high only;
            # medium still needs explicit ack unless force is set — product says force is
            # for high-risk bypass. We'll let force also proceed past medium with log.
            if medium:
                record_acceptances(store, medium, "force_override")
            return GateResult(
                allow_commit=True,
                verification=record,
                blocked_high=high,
                needs_ack_medium=medium,
                force_override=True,
                reason="force override of high-risk blockers",
                claimed_complete=record.meaningful_pass,
            )

        # Interactive explicit override (never default / never yes-always)
        ok = io.confirm_ask(
            "OVERRIDE: force commit despite high-risk blockers? This will be logged.",
            default="n",
            explicit_yes_required=True,
            subject=subject,
        )
        if ok:
            record_acceptances(store, high, "force_override")
            if medium:
                record_acceptances(store, medium, "force_override")
            return GateResult(
                allow_commit=True,
                verification=record,
                blocked_high=high,
                needs_ack_medium=medium,
                force_override=True,
                reason="user forced commit past high-risk blockers",
                claimed_complete=False,
            )
        return GateResult(
            allow_commit=False,
            verification=record,
            blocked_high=high,
            needs_ack_medium=medium,
            reason="high-risk blockers",
        )

    if medium:
        subject = _format_node_lines(medium)
        io.tool_warning(
            f"{len(medium)} medium-risk issue(s) require explicit acknowledgment "
            f"before commit:\n{subject}"
        )
        ok = io.confirm_ask(
            "Acknowledge medium-risk nodes and proceed with commit?",
            default="n",
            explicit_yes_required=True,
            subject=subject,
        )
        if not ok:
            return GateResult(
                allow_commit=False,
                verification=record,
                needs_ack_medium=medium,
                reason="medium-risk not acknowledged",
            )
        record_acceptances(store, medium, "medium_ack")
        return GateResult(
            allow_commit=True,
            verification=record,
            needs_ack_medium=[],
            acknowledged_medium=list(medium),
            reason="medium-risk explicitly acknowledged",
            claimed_complete=record.meaningful_pass,
        )

    if not record.meaningful_pass:
        # Safety net — should have been high-blocked above
        return GateResult(
            allow_commit=False,
            verification=record,
            reason="verification did not meaningfully pass",
        )

    # Reset retry counters on success; allow dirty-commits again
    coder._z_verify_gen_attempts = 0
    coder._z_verify_fix_attempts = 0
    coder._z_auto_act_attempts = 0
    coder._z_gate_hold_dirty = False
    return GateResult(
        allow_commit=True,
        verification=record,
        reason="verification passed; no blocking uncertainties",
        claimed_complete=True,
    )


def bind_acceptances_to_commit(
    store: UncertaintyStore,
    node_ids: Set[str],
    commit_hash: str,
) -> None:
    for nid in node_ids:
        node = store.get(nid)
        if not node:
            continue
        if node.signals.get("gate_accepted"):
            node.signals["gate_accepted_commit"] = commit_hash
            store.save_local()
