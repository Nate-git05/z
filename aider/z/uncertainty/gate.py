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
    NodeType.UNVALIDATED_CONFIG,
    NodeType.FAILURE_BLIND_SPOT,
    NodeType.FRAGILE_LOGIC,
    NodeType.FAILURE_ABSORPTION,
    NodeType.PATTERN_COMPANION_GAP,
    NodeType.ESTABLISHED_SOLUTION_GAP,
    NodeType.CONCURRENCY_RACE,
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
    - Dependency fabrication → always High (never downgrade)
    - Explicit High risk_tier → High
    - Actionable Medium types → Medium
    - Everything else → Low (no block)
    """
    if node.signals.get("verification_blocked"):
        return Tier.HIGH
    if node.type == NodeType.DEPENDENCY_FABRICATION or node.signals.get(
        "dependency_fabrication"
    ):
        return Tier.HIGH
    if node.type == NodeType.ABSORBED_FAILURE or node.signals.get("absorbed_failure"):
        return Tier.HIGH
    if node.type == NodeType.GETATTR_SHORTCUT or node.signals.get("getattr_shortcut"):
        return Tier.HIGH
    # Taxonomy hits: hard-block only when the named pattern is marked trusted
    if node.type == NodeType.FAILURE_ABSORPTION or node.signals.get("failure_absorption"):
        if node.signals.get("absorption_hard_block"):
            return Tier.HIGH
        if (node.risk_tier or Tier.LOW) == Tier.MEDIUM:
            return Tier.MEDIUM
        return Tier.LOW
    if node.type == NodeType.WEAK_TEST or node.signals.get("mutation_survivors"):
        return Tier.HIGH
    if node.signals.get("auto_fix_exhausted"):
        return Tier.HIGH
    if node.signals.get("tests_passed") is False and node.type == NodeType.MISSING_TEST:
        return Tier.HIGH
    if node.type == NodeType.REQUIREMENT_GAP:
        from .evidence_strategy import hard_block_kind

        req_status = node.signals.get("requirement_status") or ""
        req_kind = (node.signals.get("requirement_kind") or "product").lower()
        # Unverifiable = honest "no check yet" — Low/informational, never hard-block
        if req_status == "Unverifiable" or node.signals.get("unverifiable"):
            return Tier.LOW
        # Noise circuit: chronically unresolved detector — never hard-block
        if node.signals.get("detector_noisy"):
            return Tier.LOW
        # Process/decision/verification wording gaps stay informational Low
        if req_kind in ("process", "decision", "verification"):
            return Tier.LOW
        if req_status == "Not Addressed":
            # Only kinds with a trusted verifier promote to hard-block
            if hard_block_kind(req_kind):
                return Tier.HIGH
            # Reviewable Medium (docs/quality) until that verifier is promoted
            return Tier.MEDIUM
        if req_status == "Partially Addressed":
            return Tier.MEDIUM
        return Tier.LOW
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
    preexisting = list(getattr(record, "relevant_preexisting", None) or [])
    relevant_note = ""
    if record.failure_kind == "relevant_tests" or (
        preexisting and record.relevant_passed is False
    ):
        relevant_note = (
            "\nMANDATORY pre-existing relevant tests were discovered and must pass — "
            "a newly written test in a different directory does NOT replace them:\n"
            + "\n".join(f"  - {p}" for p in preexisting[:12])
            + "\nUpdate those established tests (or the implementation) to match the "
            "intentional behavior change. Do not only add parallel coverage elsewhere.\n"
        )
    return (
        "Z verification gate: the test suite failed after your edits"
        f"{f' to {files}' if files else ''}.\n"
        f"Command: {record.command}\n"
        f"Exit code: {record.exit_code}\n"
        f"Discovered tests: {record.tests_discovered}\n"
        f"{relevant_note}"
        f"Output (excerpt):\n{excerpt}\n\n"
        "ALLOWED fixes: correct the implementation/tests under review; "
        "install a real declared dependency (pip install / requirements).\n"
        "Trace each failure to its actual cause before changing production code. "
        "If a test helper/fixture/args() namespace is missing a newly added field, "
        "update that helper — do not paper over it in production with "
        "getattr(obj, 'new_field', default) / permissive defaults just to turn "
        "the suite green. getattr(..., default) is only for deliberate "
        "backward-compatibility, never a shortcut around a red test.\n"
        "FORBIDDEN without human approval: creating a local package/file with "
        "the same name as a missing third-party library (e.g. freezegun/__init__.py "
        "that only satisfies imports); editing unrelated conftest/CI to hide "
        "import errors; skipping or disabling tests to go green; "
        "getattr/hasattr fallbacks for constructor params you just introduced.\n"
        "If install fails, STOP and report the exact error — do not fabricate a stand-in.\n"
        "Do not claim completion while tests are red."
    )


def _confirm_relevant_tests_checkpoint(
    io,
    record: VerificationRecord,
    *,
    force: bool = False,
) -> Optional[str]:
    """
    Human-visible checkpoint: list discovered pre-existing tests, confirm run,
    and ask whether a dedicated new test is also wanted.

    Returns an optional reflect_message when the user wants a dedicated new test
    added after the mandatory pre-existing run.
    """
    preexisting = list(getattr(record, "relevant_preexisting", None) or [])
    if not preexisting:
        return None
    if force:
        return None
    # Avoid re-prompting every gate loop in the same session
    if getattr(io, "_z_relevant_tests_acked", False):
        return None

    lines = [f"  - {p}" for p in preexisting[:20]]
    if len(preexisting) > 20:
        lines.append(f"  … and {len(preexisting) - 20} more")
    subject = "\n".join(lines)
    status = (
        "PASSED"
        if record.relevant_passed is True
        else "FAILED"
        if record.relevant_passed is False
        else "PENDING"
    )
    io.tool_warning(
        f"Found {len(preexisting)} existing test file(s) covering this module "
        f"(status={status}). Running them is mandatory — new tests elsewhere "
        f"do not replace them:\n{subject}"
    )
    # Informational ack that discovery happened (not a silent skip)
    io.confirm_ask(
        "Acknowledge these pre-existing tests were discovered and must stay green?",
        default="y",
        subject=subject,
    )
    setattr(io, "_z_relevant_tests_acked", True)

    want_new = io.confirm_ask(
        "Would you also like a dedicated new test added for this specific fix?",
        default="n",
        explicit_yes_required=True,
        subject=subject,
    )
    if want_new and record.meaningful_pass:
        files = ", ".join(preexisting[:6])
        return (
            "Z verification gate: pre-existing relevant tests already cover this "
            f"module ({files}).\n"
            "The human asked for an ADDITIONAL dedicated test for this specific fix.\n"
            "Add one focused test NEXT TO the established test file(s) above — "
            "follow that directory/naming convention. Do not create a parallel "
            "tree in a different folder. Keep the pre-existing tests green."
        )
    return None


def _reflect_fix_races(record: VerificationRecord, edited: Sequence[str]) -> str:
    """Distinct reflect path for concurrency / race-detector failures."""
    files = ", ".join(list(edited)[:6])
    cmp_ = getattr(record, "race_comparison", None) or {}
    excerpt = (record.output_excerpt or record.error or "")[-1500:]
    return (
        "Z verification gate: CONCURRENCY / RACE DETECTOR failed "
        f"for {files or 'this change'}.\n"
        f"Outcome: {cmp_.get('outcome')}\n"
        f"Before races: {cmp_.get('before_races')} → After: {cmp_.get('after_races')}\n"
        f"Tool: {cmp_.get('tool_id')}\n"
        f"Summary: {cmp_.get('summary')}\n"
        f"Output excerpt:\n{excerpt}\n\n"
        "This is NOT an ordinary test failure. Do NOT burn retries on unrelated "
        "refactors.\n\n"
        "REQUIRED:\n"
        "1. Re-read the shared data structures (queues, vectors, indexes) involved "
        "in the remaining races — a textbook atomic fix can still leave other races.\n"
        "2. Keep using the same stress + race detector command for before/after "
        "comparison; require a real reduction (ideally to zero).\n"
        "3. Treat even a clean sanitizer run as reduced confidence, not proof of "
        "absence — races are non-deterministic.\n"
        "Do not claim completion while the race detector shows no improvement."
    )


def _reflect_fix_compiler(record: VerificationRecord, edited: Sequence[str]) -> str:
    """Distinct reflect path: compiler/type errors are not 'tweak the tests'."""
    excerpt = (record.output_excerpt or record.error or "")[-1800:]
    files = ", ".join(list(edited)[:6])
    kind = record.failure_kind or getattr(record.state, "value", "TYPECHECK")
    return (
        "Z verification gate: COMPILER / TYPECHECK failed "
        f"({kind}){f' for {files}' if files else ''}.\n"
        f"Command: {record.command}\n"
        f"Exit code: {record.exit_code}\n"
        f"Output (excerpt):\n{excerpt}\n\n"
        "This is NOT a generic test failure. Do NOT burn retries on unrelated "
        "import-path / encoding / test-harness edits.\n\n"
        "REQUIRED next step:\n"
        "1. Read each error as a precise instruction "
        "(e.g. Property 'worktree' does not exist on type 'Context').\n"
        "2. Open the REAL type/interface declaration in this repo (or the pinned "
        "dependency version) and re-read its declared members/API.\n"
        "3. Change the implementation to match ground truth — remove invented "
        "fields/methods, or use the actual API that exists on this pin.\n\n"
        "FORBIDDEN: guessing plausible field names; switching to a different "
        "Effect/stdlib API from training priors without checking the pinned "
        "version; patching around the error without re-reading the type.\n"
        "Do not claim completion while the typechecker is red."
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
    # Extract symbols + brand-new paths so relevant-test discovery can find
    # nested pre-existing files (e.g. implementations/test_model_retry.py).
    symbols: List[str] = []
    new_files: List[str] = list(getattr(engine.ctx, "new_files_this_turn", None) or [])
    try:
        from .sibling_traits import new_files_from_diff

        diff_text = getattr(engine.ctx, "last_diff", None) or ""
        for nf in new_files_from_diff(diff_text):
            if nf not in new_files:
                new_files.append(nf)
        contents = {}
        for path in edited_list:
            try:
                rel = coder.get_rel_fname(path)
            except Exception:
                rel = str(path)
            abs_p = root / rel
            if abs_p.is_file():
                try:
                    contents[rel] = abs_p.read_text(encoding="utf-8", errors="ignore")[
                        :20000
                    ]
                except OSError:
                    pass
        if contents and hasattr(engine, "_extract_symbols"):
            symbols = list(engine._extract_symbols(contents) or [])
    except Exception:
        pass

    record, relevant = verify_edits(
        root,
        edited_list,
        test_cmd=getattr(coder, "test_cmd", None),
        symbols=symbols,
        new_files=new_files,
        verbose=bool(getattr(coder, "verbose", False)),
        error_print=io.tool_error,
    )

    # Concurrency-relevant diffs → mandatory race detector (before/after) when available
    from .verify import COMPILER_VERIFY_STATES

    race_cmp = None
    try:
        from .concurrency_checks import (
            analyze_concurrency_change,
            concurrency_nodes_from_comparison,
            tag_concurrency_relevant,
        )
        from .risk import collect_base_signals

        diff_for_race = getattr(engine.ctx, "last_diff", None) or ""
        tag = tag_concurrency_relevant(diff_for_race, edited_list)
        record.concurrency_relevant = tag.relevant
        if tag.relevant and record.state not in COMPILER_VERIFY_STATES:
            # Only spend detector budget when ordinary verify isn't already a
            # compiler failure; still run when tests failed so races surface.
            io.tool_warning(
                "Concurrency-relevant change detected — running race detector "
                f"({'; '.join(tag.reasons[:2]) or 'threading primitives in diff'})."
            )
            race_cmp = analyze_concurrency_change(
                root,
                diff=diff_for_race,
                edited=edited_list,
                verbose=bool(getattr(coder, "verbose", False)),
                error_print=io.tool_error,
            )
            record.race_comparison = race_cmp.to_dict()
            engine.record_execution(
                f"race analysis outcome={race_cmp.outcome} "
                f"before={race_cmp.before.race_count if race_cmp.before else None} "
                f"after={race_cmp.after.race_count if race_cmp.after else None} "
                f"tool={race_cmp.tool.tool_id if race_cmp.tool else None}"
            )
            if race_cmp.blocks_commit:
                record.failure_kind = "race_detection"
                record.state = VerifyState.RACE_DETECTED
                record.passed = False
                record.error = race_cmp.summary
                if race_cmp.after and race_cmp.after.output_excerpt:
                    record.output_excerpt = race_cmp.after.output_excerpt
            # Always emit an honest node (clean runs get reduced-confidence label)
            sig = collect_base_signals(edited_list)
            sig.concurrency_relevant = True
            sig.race_detector_ran = race_cmp.tool_available
            sig.race_detector_outcome = race_cmp.outcome
            nodes = concurrency_nodes_from_comparison(
                race_cmp,
                signals=sig,
                files=edited_list,
                task_id=getattr(engine.ctx, "current_task_id", None),
                task_title=getattr(engine.ctx, "current_task_title", None),
                created_by_session=getattr(engine.ctx, "session_id", None),
            )
            if nodes:
                store.add_many(nodes)
    except Exception as err:  # noqa: BLE001
        if getattr(coder, "verbose", False):
            io.tool_warning(f"Concurrency race analysis skipped: {err}")

    coder.last_verification = record
    coder.test_outcome = bool(record.meaningful_pass)
    try:
        engine.ctx.last_verification = record
        engine.record_execution(
            f"verification state={getattr(record.state, 'value', record.state)} "
            f"discovered={record.tests_discovered} exit={record.exit_code} "
            f"cmd={record.command} relevant_preexisting="
            f"{len(record.relevant_preexisting or [])} "
            f"relevant_passed={record.relevant_passed}"
        )
        # Surface ModuleNotFoundError into the session log for fabrication checks
        from aider.z.deps import extract_missing_modules

        missing = extract_missing_modules(
            "\n".join(
                [
                    record.output_excerpt or "",
                    record.error or "",
                    record.smoke_detail or "",
                ]
            )
        )
        for mod in sorted(missing):
            engine.record_execution(f"ModuleNotFoundError: No module named '{mod}'")
    except Exception:
        pass

    gen_attempts = int(getattr(coder, "_z_verify_gen_attempts", 0) or 0)
    fix_attempts = int(getattr(coder, "_z_verify_fix_attempts", 0) or 0)
    force = _force_requested(coder)

    # Branch on structured VerifyState / suite discovery — NOT on empty
    # find_relevant_tests(). "2 failed, 7 passed" must never become "no tests".
    state = record.state or VerifyState.NOT_RUN
    discovered = record.tests_discovered
    needs_race_fix = (
        state == VerifyState.RACE_DETECTED or record.failure_kind == "race_detection"
    )
    needs_compiler_fix = (
        not needs_race_fix
        and (
            state in COMPILER_VERIFY_STATES
            or bool(getattr(record, "is_compiler_failure", False))
        )
    )
    needs_generate = (
        not needs_compiler_fix
        and not needs_race_fix
        and (
            state
            in (VerifyState.NO_TESTS, VerifyState.RUNNER_MISSING, VerifyState.NOT_RUN)
            or (not record.ran)
            or record.zero_tests
            or (discovered is not None and discovered == 0)
        )
    )
    needs_fix = (
        not needs_compiler_fix
        and not needs_race_fix
        and (
            state in (VerifyState.TESTS_FAILED, VerifyState.COLLECTION_FAILED)
            or (
                record.ran
                and not record.meaningful_pass
                and (discovered or 0) > 0
            )
        )
    )
    # Prefer fix when we know tests existed and failed
    if needs_fix and (discovered or 0) > 0:
        needs_generate = False
    # Pre-existing relevant tests failed/unrun — never "generate new tests" instead
    if record.failure_kind == "relevant_tests" or (
        record.relevant_preexisting
        and record.relevant_passed is False
    ):
        needs_fix = True
        needs_generate = False

    # Always surface discovered pre-existing tests (visibility); interactive
    # "want a dedicated new test?" only when the mandatory run is green.
    if record.relevant_preexisting and not getattr(io, "_z_relevant_listed", False):
        listed = "\n".join(f"  - {p}" for p in record.relevant_preexisting[:16])
        io.tool_warning(
            f"Found {len(record.relevant_preexisting)} existing test file(s) "
            f"covering this module — running them is mandatory:\n{listed}"
        )
        setattr(io, "_z_relevant_listed", True)
    if (
        not needs_compiler_fix
        and not needs_fix
        and not needs_generate
        and record.meaningful_pass
    ):
        try:
            extra_reflect = _confirm_relevant_tests_checkpoint(
                io, record, force=force
            )
        except Exception:
            extra_reflect = None
        if extra_reflect:
            return GateResult(
                allow_commit=False,
                reflect_message=extra_reflect,
                verification=record,
                reason="human requested dedicated new test beside pre-existing ones",
            )

    if needs_race_fix and not record.meaningful_pass:
        cmp_ = record.race_comparison or {}
        node = _upsert_verification_node(
            store,
            title=(
                f"Concurrency race detector — {cmp_.get('outcome', 'failed')} "
                "(commit blocked)"
            ),
            summary=(
                cmp_.get("summary")
                or "Commit blocked: concurrency change did not improve under "
                "the race detector before/after comparison."
            ),
            explanation=(
                f"State: {state.value}\n"
                f"Outcome: {cmp_.get('outcome')}\n"
                f"Before→After races: {cmp_.get('before_races')}→"
                f"{cmp_.get('after_races')}\n"
                f"Tool: {cmp_.get('tool_id')}\n"
                f"{record.output_excerpt[-1500:]}"
            ),
            files=edited_list,
            record=record,
            task_id=getattr(engine.ctx, "current_task_id", None),
            task_title=getattr(engine.ctx, "current_task_title", None),
        )
        node.signals["concurrency_race"] = True
        node.signals["race_outcome"] = cmp_.get("outcome")
        node.signals["verification_blocked"] = True
        store.save_local()
        coder._z_gate_hold_dirty = True
        if not force and fix_attempts < MAX_TEST_FIX_ATTEMPTS:
            coder._z_verify_fix_attempts = fix_attempts + 1
            io.tool_warning(
                "Verification: RACE_DETECTED — re-read shared structures; "
                "before/after sanitizer must show a real reduction."
            )
            return GateResult(
                allow_commit=False,
                reflect_message=_reflect_fix_races(record, edited_list),
                verification=record,
                blocked_high=[node],
                reason="race detector — no improvement / regression",
            )
        if fix_attempts >= MAX_TEST_FIX_ATTEMPTS and not force:
            node.title = (
                f"Auto-fix exhausted — races still present after {fix_attempts} "
                "attempts"
            )
            node.signals["auto_fix_exhausted"] = True
            store.save_local()
            reason = (
                f"Auto-fix exhausted after {fix_attempts} attempts; "
                "race detector still shows no improvement.\n"
                f"{_last_failure_excerpt(record)}"
            )
            io.tool_error(f"Commit blocked by Z verification gate. {reason}")
            return GateResult(
                allow_commit=False,
                verification=record,
                blocked_high=[node],
                reason=reason,
            )

    elif needs_compiler_fix and not record.meaningful_pass:
        kind = record.failure_kind or state.value
        node = _upsert_verification_node(
            store,
            title=f"Compiler/typecheck failed ({kind}) — commit blocked",
            summary=(
                "Commit blocked: package typecheck/build reported errors. "
                "Re-read the real type definitions — do not guess field names."
            ),
            explanation=(
                f"State: {state.value}\n"
                f"Failure kind: {kind}\n"
                f"Command: {record.command}\n"
                f"Exit: {record.exit_code}\n"
                f"Prechecks: {record.prechecks!r}\n"
                f"{record.output_excerpt[-1500:]}"
            ),
            files=edited_list,
            record=record,
            task_id=getattr(engine.ctx, "current_task_id", None),
            task_title=getattr(engine.ctx, "current_task_title", None),
        )
        node.signals["compiler_errors"] = True
        node.signals["failure_kind"] = kind
        store.save_local()
        coder._z_gate_hold_dirty = True
        if not force and fix_attempts < MAX_TEST_FIX_ATTEMPTS:
            coder._z_verify_fix_attempts = fix_attempts + 1
            io.tool_warning(
                f"Verification: {state.value} — compiler/type error; "
                "re-read declarations before editing."
            )
            return GateResult(
                allow_commit=False,
                reflect_message=_reflect_fix_compiler(record, edited_list),
                verification=record,
                blocked_high=[node],
                reason="compiler/typecheck failed — reflect to fix types",
            )
        if fix_attempts >= MAX_TEST_FIX_ATTEMPTS and not force:
            node.title = (
                f"Auto-fix exhausted — typecheck still failing after "
                f"{fix_attempts} attempts"
            )
            node.signals["auto_fix_exhausted"] = True
            node.signals["fix_attempts"] = fix_attempts
            store.save_local()
            reason = (
                f"Auto-fix exhausted after {fix_attempts} attempts; "
                "typecheck still failing. Re-read the real types.\n"
                f"{_last_failure_excerpt(record)}"
            )
            io.tool_error(f"Commit blocked by Z verification gate. {reason}")
            return GateResult(
                allow_commit=False,
                verification=record,
                blocked_high=[node],
                reason=reason,
            )

    elif needs_generate and not record.meaningful_pass:
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

        # Gate-level fix retries exhausted — hard-block unless force override.
        # (Force still goes through the shared high-risk override path below.)
        if fix_attempts >= MAX_TEST_FIX_ATTEMPTS and not force:
            node.title = (
                f"Auto-fix exhausted — still failing after {fix_attempts} attempts"
            )
            node.summary = (
                "Commit blocked: auto-fix retries exhausted and the suite is still red. "
                "A human needs to look."
            )
            node.signals["auto_fix_exhausted"] = True
            node.signals["fix_attempts"] = fix_attempts
            failure = _last_failure_excerpt(record)
            node.explanation = (
                f"{node.explanation}\n\nAuto-fix exhausted after {fix_attempts} "
                f"gate retry attempt(s).\n{failure}"
            )
            store.save_local()
            reason = (
                f"Auto-fix exhausted after {fix_attempts} attempts; "
                "tests still failing. A human needs to look.\n"
                f"{failure}"
            )
            io.tool_error(f"Commit blocked by Z verification gate. {reason}")
            return GateResult(
                allow_commit=False,
                verification=record,
                blocked_high=[node],
                reason=reason,
            )

    else:
        # Meaningful pass — clear prior verification_blocked nodes for these files
        for existing in list(store.list(include_resolved=False)):
            if existing.signals.get("verification_blocked"):
                store.update_status(existing.id, NodeStatus.RESOLVED)

        # Scoped mutation check (Codex #11) — only after green suite
        try:
            from .mutation import mutation_nodes_from_result, run_mutation_check
            from .risk import collect_base_signals

            rels_for_mut = []
            for path in edited_list:
                try:
                    rels_for_mut.append(coder.get_rel_fname(path))
                except Exception:
                    rels_for_mut.append(str(path))
            mut = run_mutation_check(
                root,
                edited=rels_for_mut,
                relevant_tests=list(relevant or []),
                test_cmd=record.command or getattr(coder, "test_cmd", None),
                diff=getattr(engine.ctx, "last_diff", "") or "",
                max_mutations=3,
                verbose=bool(getattr(coder, "verbose", False)),
            )
            if mut.survivors:
                sig = collect_base_signals(rels_for_mut)
                weak = mutation_nodes_from_result(
                    mut,
                    signals=sig,
                    task_id=getattr(engine.ctx, "current_task_id", None),
                    task_title=getattr(engine.ctx, "current_task_title", None),
                    created_by_session=getattr(engine.ctx, "session_id", None),
                )
                store.add_many(weak)
                engine.record_execution(
                    f"mutation check: {len(mut.survivors)} survivor(s) / "
                    f"{mut.attempted} attempted"
                )
                io.tool_warning(
                    f"Mutation check: {len(mut.survivors)} weakening(s) on new "
                    "lines still left tests green — Weak Test Suite raised."
                )
        except Exception as err:  # noqa: BLE001
            if getattr(coder, "verbose", False):
                io.tool_warning(f"Mutation check skipped: {err}")

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
        new_nodes = engine.analyze_edits(
            rels,
            tests_passed=coder.test_outcome,
            diff=getattr(engine.ctx, "last_diff", None),
        )
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
        fab_nodes = [
            n
            for n in high
            if n.type == NodeType.DEPENDENCY_FABRICATION
            or n.signals.get("dependency_fabrication")
        ]
        if fab_nodes:
            pkgs = sorted(
                {
                    str(n.signals.get("fabricated_package") or "")
                    for n in fab_nodes
                    if n.signals.get("fabricated_package")
                }
            )
            io.tool_error(
                "Dependency Fabrication detected — a local package may be shadowing "
                "a real third-party library "
                f"({', '.join(pkgs) or 'see nodes above'}).\n"
                "Remove the local stand-in and install the real dependency. "
                "A generic force-commit is not enough for this finding."
            )

        if force and not fab_nodes:
            io.tool_warning(
                "FORCE COMMIT: bypassing high-risk block (--force-commit / Z_FORCE_COMMIT). "
                "Override will be logged on the nodes."
            )
            record_acceptances(store, high, "force_override")
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
        if force and fab_nodes:
            io.tool_error(
                "FORCE COMMIT refused: Dependency Fabrication cannot be bypassed with "
                "--force-commit / Z_FORCE_COMMIT alone. Delete the local shadow package "
                "or give an explicit typed acknowledgment in the interactive prompt."
            )

        # Interactive override — dependency fabrication needs a distinct ack
        if fab_nodes:
            pkg = str(
                fab_nodes[0].signals.get("fabricated_package") or "the package"
            )
            ok = io.confirm_ask(
                f"OVERRIDE DEPENDENCY FABRICATION: I accept committing a local "
                f"'{pkg}' stand-in that shadows the real third-party library. "
                "This is dangerous and will be logged. Type yes only if intentional.",
                default="n",
                explicit_yes_required=True,
                subject=subject,
            )
        else:
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
                reason=(
                    "user forced commit past dependency fabrication"
                    if fab_nodes
                    else "user forced commit past high-risk blockers"
                ),
                claimed_complete=False,
            )
        return GateResult(
            allow_commit=False,
            verification=record,
            blocked_high=high,
            needs_ack_medium=medium,
            reason=(
                "dependency fabrication blockers"
                if fab_nodes
                else "high-risk blockers"
            ),
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


def _last_failure_excerpt(record: Optional[VerificationRecord], reflect_message: str = "") -> str:
    """Pull the most useful failure text for a human (test name + assertion diff)."""
    parts: List[str] = []
    if record is not None:
        if record.command:
            parts.append(f"Command: {record.command}")
        if record.exit_code is not None:
            parts.append(f"Exit code: {record.exit_code}")
        if record.tests_failed is not None or record.tests_discovered is not None:
            parts.append(
                f"Tests: failed={record.tests_failed} / "
                f"passed={record.tests_passed} / discovered={record.tests_discovered}"
            )
        excerpt = (record.output_excerpt or record.error or "").strip()
        if excerpt:
            parts.append("Last failure (verbatim):\n" + excerpt[-1800:])
    reflect = (reflect_message or "").strip()
    if reflect and "Output (excerpt)" in reflect:
        # Prefer the structured reflect body when verification record is thin
        if not record or not (record.output_excerpt or record.error):
            parts.append("Pending fix prompt (excerpt):\n" + reflect[-1800:])
    elif reflect and not parts:
        parts.append("Pending reflection (excerpt):\n" + reflect[-1200:])
    return "\n".join(parts) if parts else "(no failure excerpt captured)"


def report_auto_fix_exhaustion(
    coder,
    *,
    max_reflections: int,
    pending_reflect: str = "",
) -> Optional[UncertaintyNode]:
    """
    Reflection-loop cap with tests still red — surface like every other gate stop.

    Previously this path only printed "Only N reflections allowed, stopping." and
    left a broken working tree with no uncertainty node / commit-blocked message.
    """
    io = getattr(coder, "io", None)
    engine = getattr(coder, "uncertainty_engine", None)
    store = getattr(coder, "uncertainty_store", None)
    record = getattr(coder, "last_verification", None)
    if record is None and engine is not None:
        record = getattr(engine.ctx, "last_verification", None)

    tests_still_failing = False
    if record is not None:
        tests_still_failing = bool(record.ran and not record.meaningful_pass)
    if getattr(coder, "test_outcome", None) is False:
        tests_still_failing = True
    pending = pending_reflect or getattr(coder, "reflected_message", None) or ""
    if not tests_still_failing and pending:
        # Heuristic: verify-gate / auto-test reflect still queued
        low = pending.lower()
        if any(
            s in low
            for s in (
                "test suite failed",
                "verification gate",
                "fix failing",
                "tests failed",
                "attempt to fix test",
                "compiler / typecheck",
                "typecheck failed",
                "property '",
                "does not exist on type",
            )
        ):
            tests_still_failing = True

    if not tests_still_failing:
        if io is not None:
            io.tool_warning(
                f"Only {max_reflections} reflections allowed, stopping."
            )
        return None

    failure = _last_failure_excerpt(
        record if isinstance(record, VerificationRecord) else None,
        pending,
    )
    edited = list(getattr(coder, "aider_edited_files", None) or [])
    files = [str(f) for f in edited[:12]]

    node = None
    if store is not None:
        # Prefer a real VerificationRecord; synthesize a stub if missing
        if not isinstance(record, VerificationRecord):
            record = VerificationRecord(
                ran=True,
                passed=False,
                state=VerifyState.TESTS_FAILED,
                output_excerpt=failure[-1500:],
                error="auto-fix exhausted with tests still failing",
            )
        node = _upsert_verification_node(
            store,
            title=(
                f"Auto-fix exhausted — still failing after "
                f"{max_reflections} attempts"
            ),
            summary=(
                f"Commit blocked: auto-fix hit the reflection cap "
                f"({max_reflections}) and the suite is still red. "
                "A human needs to look."
            ),
            explanation=(
                f"Reflection loop exhausted after {max_reflections} attempts "
                "without a meaningful test pass. The working tree may contain a "
                "partial/broken fix from the last retry.\n\n"
                f"{failure}"
            ),
            files=files,
            record=record,
            task_id=getattr(getattr(engine, "ctx", None), "current_task_id", None),
            task_title=getattr(getattr(engine, "ctx", None), "current_task_title", None),
        )
        node.signals["auto_fix_exhausted"] = True
        node.signals["reflection_cap"] = max_reflections
        node.why_uncertain = (
            "Auto-fix retries were exhausted; remaining failures need a human."
        )
        node.what_could_go_wrong = (
            "Silent stop leaves regressions (e.g. weakened validation) in the "
            "working tree with no commit and no visible uncertainty finding."
        )
        node.suggested_fix = (
            "Inspect the last failing assertion, restore any accidental "
            "regressions from auto-fix retries, and re-run the suite."
        )
        node.suggested_prompt = (
            f"Auto-fix exhausted after {max_reflections} attempts. "
            "Do not claim completion. Fix the remaining failure below, "
            "or stop and ask a human.\n\n"
            f"{failure}"
        )
        store.save_local()
        if engine is not None:
            try:
                engine.record_execution(
                    f"auto-fix exhausted after {max_reflections} reflections; "
                    "tests still failing"
                )
            except Exception:
                pass

    detail = (
        f"Auto-fix exhausted after {max_reflections} attempts; "
        "tests still failing. A human needs to look.\n"
        f"{failure}"
    )
    blocked_msg = f"Commit blocked by Z verification gate. {detail}"
    if io is not None:
        io.tool_error(
            f"Only {max_reflections} reflections allowed, stopping — "
            "auto-fix exhausted with tests still failing."
        )
        io.tool_error(blocked_msg)
        if node is not None:
            io.tool_error(
                f"High-risk issue raised: {node.title}\n"
                f"  {node.summary}"
            )
    move_back = getattr(coder, "move_back_cur_messages", None)
    if callable(move_back):
        try:
            move_back(blocked_msg)
        except Exception:
            pass
    # Hold dirty-commits — tree is not verified
    coder._z_gate_hold_dirty = True
    return node


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
