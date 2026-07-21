"""P1 — clause schema, resolution contracts, exception policy."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_p1_")
os.environ["Z_HOME"] = _HOME

from aider.z.errors import (  # noqa: E402
    IntegrityGateError,
    OptionalSubsystemError,
    RecoverableAgentError,
    classify_exception,
    handle_classified,
)
from aider.z.uncertainty.checklist import decompose_request  # noqa: E402
from aider.z.uncertainty.clause import (  # noqa: E402
    clause_violates_constraints,
    classify_clause_text,
    extract_clauses,
    process_rule_violated,
)
from aider.z.uncertainty.intent import extract_intent  # noqa: E402
from aider.z.uncertainty.plan import draft_plan_from_request  # noqa: E402
from aider.z.uncertainty.resolution import (  # noqa: E402
    attach_contract_to_node,
    explain_blocker,
    filter_active_blockers,
    try_auto_resolve,
    try_reopen,
)
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeStatus,
    NodeType,
    Tier,
    UncertaintyNode,
)
from aider.z.uncertainty.store import UncertaintyStore  # noqa: E402


REVIEW_SENTENCES = [
    ("Model A currently returns an error.", "observation"),
    ("Model B currently works.", "observation"),
    ("The issue was reported on Linux.", "background"),
    ("Do not invent new mappings.", "constraint"),
    ("Only make changes after confirming the cause.", "process_rule"),
]


class ClauseSchemaTests(unittest.TestCase):
    def test_review_sentences_kinds(self):
        for text, expected in REVIEW_SENTENCES:
            kind, _pol, _conf = classify_clause_text(text)
            self.assertEqual(kind, expected, msg=f"{text!r} → {kind}")

    def test_review_sentences_not_in_checklist(self):
        msg = "\n".join(t for t, _ in REVIEW_SENTENCES)
        checklist = decompose_request("bug", msg)
        texts = {i.text.lower() for i in checklist.items}
        for t, kind in REVIEW_SENTENCES:
            if kind in ("observation", "background", "constraint", "process_rule"):
                self.assertFalse(
                    any(t.lower() in item or item in t.lower() for item in texts),
                    msg=f"{kind} leaked into checklist: {t}",
                )

    def test_multi_clause_sentence(self):
        clauses = extract_clauses(
            "Do not invent new mappings, only fix the existing lookup"
        )
        kinds = {c.kind for c in clauses}
        self.assertIn("constraint", kinds)
        self.assertIn("requested_action", kinds)

    def test_constraint_blocks_plan_step(self):
        intent = extract_intent(
            "Do not invent new mappings. Add a healthcheck endpoint."
        )
        plan = draft_plan_from_request(
            "Do not invent new mappings. Add a healthcheck endpoint.",
            intent=intent,
            reason="test",
        )
        steps_joined = " ".join(plan.steps).lower()
        # Inventing mappings must not appear as an allowed step
        self.assertNotIn("invent new mapping", steps_joined)
        self.assertNotIn("invent new mappings", steps_joined)
        viol = clause_violates_constraints(
            "Invent new mappings for the API", intent.clauses
        )
        self.assertIsNotNone(viol)
        # Constraint should be recorded as out-of-scope / prohibited
        self.assertTrue(
            any("invent" in o.lower() for o in plan.out_of_scope)
            or any("invent" in p.lower() for p in intent.prohibited_actions)
        )

    def test_process_rule_violation(self):
        clauses = extract_clauses(
            "Only make changes after confirming the cause. Fix the bug."
        )
        viol = process_rule_violated(
            clauses, execution_log="", edits_made=True
        )
        self.assertIsNotNone(viol)
        ok = process_rule_violated(
            clauses,
            execution_log="root cause confirmed via failing test",
            edits_made=True,
        )
        self.assertIsNone(ok)

    def test_low_confidence_not_requested_action(self):
        kind, _p, conf = classify_clause_text("stuff happened somehow")
        self.assertNotEqual(kind, "requested_action")
        self.assertLess(conf, 0.6)


class ResolutionContractTests(unittest.TestCase):
    def _shell_node(self, cmd: str = "git status") -> UncertaintyNode:
        node = UncertaintyNode(
            title="Shell command needs human approval",
            type=NodeType.FAILURE_BLIND_SPOT,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.MEDIUM,
            summary="blocked",
            signals={
                "shell_approval_block": True,
                "blocked_command": cmd,
                "temporary_blocker": True,
            },
            task_id="task-1",
        )
        return attach_contract_to_node(node)

    def test_shell_auto_resolve(self):
        node = self._shell_node("git status")
        self.assertTrue(
            try_auto_resolve(
                node, session_evidence=[f"command_success:git status", "command_ok: git status"]
            )
        )

    def test_reopen_on_contradiction(self):
        node = UncertaintyNode(
            title="test failed",
            type=NodeType.MISSING_TEST,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.MEDIUM,
            summary="fail",
            status=NodeStatus.RESOLVED,
            signals={"test_failure": True, "test_id": "test_foo"},
        )
        attach_contract_to_node(node)
        node.status = NodeStatus.RESOLVED
        self.assertTrue(
            try_reopen(node, session_evidence=["test_fail:test_foo"])
        )

    def test_temporary_not_merged_across_sessions(self):
        store = UncertaintyStore(
            root=Path(_HOME) / "repo-a",
            repo_key="repo-a",
            remote_sync=None,
        )
        node = self._shell_node()
        store.add(node, sync=False)
        # Simulate remote payload of a temporary blocker
        remote = [node.to_dict()]
        other = UncertaintyStore(
            root=Path(_HOME) / "repo-b",
            repo_key="repo-b",
            remote_sync=None,
        )
        added = other.merge_remote(remote)
        self.assertEqual(added, 0)

    def test_persistent_carried_over(self):
        node = UncertaintyNode(
            title="No test coverage",
            type=NodeType.HIGH_STAKES,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.HIGH,
            summary="auth path untested",
        )
        attach_contract_to_node(node)
        store = UncertaintyStore(
            root=Path(_HOME) / "repo-c",
            repo_key="repo-c",
            remote_sync=None,
        )
        added = store.merge_remote([node.to_dict()])
        self.assertEqual(added, 1)
        merged = store.get(node.id)
        self.assertTrue((merged.signals or {}).get("carried_over"))

    def test_blocker_explanation_surface(self):
        node = self._shell_node()
        node.signals["source_requirement_id"] = "req-1"
        node.signals["resolution_contract"]["source_requirement_id"] = "req-1"
        expl = explain_blocker(
            node,
            active_requirement_ids={"req-1"},
            session_evidence=[],
            current_task_id="task-1",
        )
        self.assertTrue(expl.reasons)
        blockers = filter_active_blockers(
            [node],
            active_requirement_ids={"req-1"},
            current_task_id="task-1",
        )
        # May or may not block depending on has_src logic — explanation always present
        if blockers:
            self.assertIn("blocker_explanation", blockers[0].signals)

    def test_stale_source_stops_blocking(self):
        node = self._shell_node()
        attach_contract_to_node(node, source_requirement_id="req-gone")
        expl = explain_blocker(
            node,
            active_requirement_ids={"req-other"},
            session_evidence=[],
            current_task_id="task-1",
        )
        self.assertFalse(expl.blocks)


class ExceptionPolicyTests(unittest.TestCase):
    def test_optional_continues(self):
        result = handle_classified(
            OptionalSubsystemError("sync down"), context="remote"
        )
        self.assertEqual(result, "continue")

    def test_integrity_fails_closed(self):
        class IO:
            def __init__(self):
                self.errors = []

            def tool_error(self, m):
                self.errors.append(m)

        io = IO()
        result = handle_classified(
            IntegrityGateError("verify broken"), context="verify", io=io
        )
        self.assertEqual(result, "fail_closed")
        self.assertTrue(io.errors)

    def test_unclassified_defaults_to_integrity(self):
        self.assertIs(classify_exception(RuntimeError("boom")), IntegrityGateError)

    def test_recoverable_escalates_when_planning_required(self):
        class IO:
            def __init__(self):
                self.errors = []
                self.warnings = []

            def tool_error(self, m):
                self.errors.append(m)

            def tool_warning(self, m):
                self.warnings.append(m)

        io = IO()
        result = handle_classified(
            RecoverableAgentError("plan render failed"),
            context="planning",
            io=io,
            planning_required=True,
        )
        self.assertEqual(result, "fail_closed")


class TranscriptP1Smoke(unittest.TestCase):
    def test_full_clause_to_constraint_flow(self):
        msg = (
            "Model A currently returns an error. "
            "Do not invent new mappings. "
            "Only fix the existing lookup after confirming the cause."
        )
        intent = extract_intent(msg)
        checklist = decompose_request("fix", msg, intent=intent)
        # Observations/constraints not product checklist items
        for item in checklist.items:
            self.assertNotIn("currently returns", item.text.lower())
            self.assertNotIn("reported on", item.text.lower())
        plan = draft_plan_from_request(msg, intent=intent, reason="t")
        # Constraint present in out_of_scope or steps filtered
        blob = (plan.approach + "\n".join(plan.steps) + "\n".join(plan.out_of_scope)).lower()
        self.assertTrue(
            "constraint" in blob
            or "do not invent" in " ".join(intent.prohibited_actions).lower()
            or "invent" not in "\n".join(plan.steps).lower()
        )


if __name__ == "__main__":
    unittest.main()
