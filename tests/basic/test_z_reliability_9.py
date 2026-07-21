"""Reliability-9: verification integrity, capabilities, journeys, completion."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_rel9_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.architecture import (  # noqa: E402
    draft_architecture_checkpoint,
)
from aider.z.uncertainty.capabilities import (  # noqa: E402
    build_capability_plan,
    infer_capabilities,
)
from aider.z.uncertainty.completion import evaluate_completion  # noqa: E402
from aider.z.uncertainty.failure_classify import classify_failure  # noqa: E402
from aider.z.uncertainty.integrity import (  # noqa: E402
    integrity_nodes_from_report,
    scan_verification_integrity,
)
from aider.z.uncertainty.journeys import (  # noqa: E402
    EVIDENCE_MULTI_SESSION_E2E,
    EVIDENCE_UNIT,
    infer_critical_journeys,
    mark_journey_evidence,
)
from aider.z.uncertainty.plan import (  # noqa: E402
    draft_plan_from_request,
    triage_for_planning,
)
from aider.z.uncertainty.schema import NodeType, RequirementItem, TaskChecklist  # noqa: E402
from aider.z.uncertainty.verify import VerificationRecord, VerifyState  # noqa: E402


MULTIPLAYER = (
    "Build a multiplayer rock-paper-scissors web app with Next.js. "
    "Two players join a lobby via QR code, challenge each other, "
    "play best-of-three with hidden choices, and return to the lobby."
)


class FailureClassifyTest(unittest.TestCase):
    def test_tsc_not_found_is_not_type_error(self):
        cls = classify_failure(
            output="sh: tsc: command not found",
            command="npm run typecheck",
            exit_code=127,
            failure_kind="typecheck",
        )
        self.assertEqual(cls.layer, "command_not_found")
        self.assertIn("environment", cls.backtrack_target)

    def test_ts_property_error_is_type_error(self):
        cls = classify_failure(
            output="error TS2339: Property 'worktree' does not exist on type 'Context'.",
            command="tsc --noEmit",
            exit_code=2,
            failure_kind="typecheck",
        )
        self.assertEqual(cls.layer, "type_error")

    def test_assertion_failure(self):
        cls = classify_failure(
            output="AssertionError: Expected 'round_win' but got 'win'",
            failure_kind="test",
        )
        self.assertEqual(cls.layer, "assertion")


class IntegrityTest(unittest.TestCase):
    def test_blocks_typecheck_noop_after_failure(self):
        diff = (
            "diff --git a/package.json b/package.json\n"
            "--- a/package.json\n"
            "+++ b/package.json\n"
            "@@ -1,5 +1,5 @@\n"
            '   "scripts": {\n'
            '-    "typecheck": "tsc --noEmit",\n'
            '+    "typecheck": "exit 0",\n'
            '     "test": "bun test"\n'
        )
        report = scan_verification_integrity(
            diff, edited=["package.json"], had_prior_failure=True
        )
        self.assertTrue(report.blocked)
        kinds = {f.kind for f in report.findings}
        self.assertTrue(kinds & {"script_noop", "script_removed"})
        nodes = integrity_nodes_from_report(report)
        self.assertEqual(nodes[0].type, NodeType.VERIFICATION_INTEGRITY)
        self.assertEqual(nodes[0].risk_tier.value, "High")

    def test_blocks_ts_ignore_after_failure(self):
        diff = (
            "diff --git a/src/app.ts b/src/app.ts\n"
            "--- a/src/app.ts\n"
            "+++ b/src/app.ts\n"
            "@@ -1,3 +1,4 @@\n"
            "+// @ts-ignore\n"
            " export const x = 1\n"
        )
        report = scan_verification_integrity(diff, had_prior_failure=True)
        self.assertTrue(report.blocked)
        self.assertTrue(any(f.kind == "ts_ignore_added" for f in report.findings))


class CapabilityPlanTest(unittest.TestCase):
    def test_infers_multiplayer_capabilities(self):
        caps = infer_capabilities(MULTIPLAYER)
        ids = {c.id for c in caps}
        self.assertIn("multi_session_browser", ids)
        self.assertIn("shared_server_state", ids)
        self.assertIn("nextjs_impl", ids)

    def test_gaps_when_no_skills(self):
        plan = build_capability_plan(MULTIPLAYER, skill_capabilities=[], skill_ids=[])
        self.assertTrue(plan.coverage_gaps)
        self.assertTrue(plan.compensation)
        self.assertTrue(
            any("multi-session" in c.lower() or "browser" in c.lower() for c in plan.compensation)
        )


class ArchitectureTest(unittest.TestCase):
    def test_checkpoint_for_multiplayer(self):
        cp = draft_architecture_checkpoint(MULTIPLAYER)
        self.assertTrue(cp.items)
        self.assertTrue(cp.recommended_layers)
        # Shared state should be heuristically known
        shared = next(i for i in cp.items if i.id == "shared_state")
        self.assertEqual(shared.status, "known")


class JourneyTest(unittest.TestCase):
    def test_multiplayer_journey_steps(self):
        plan = infer_critical_journeys(MULTIPLAYER)
        self.assertTrue(plan.journeys)
        j = plan.journeys[0]
        self.assertEqual(j.required_evidence_type, EVIDENCE_MULTI_SESSION_E2E)
        self.assertGreaterEqual(len(j.steps), 8)
        self.assertFalse(j.has_passing_evidence)

    def test_unit_test_cannot_verify_multi_session(self):
        plan = infer_critical_journeys(MULTIPLAYER)
        jid = plan.journeys[0].id
        mark_journey_evidence(
            plan, jid, evidence_type=EVIDENCE_UNIT, notes="unit of respondChallenge", passed=True
        )
        self.assertFalse(plan.journeys[0].has_passing_evidence)
        self.assertEqual(plan.journeys[0].status, "unverified")

    def test_correct_evidence_verifies(self):
        plan = infer_critical_journeys(MULTIPLAYER)
        jid = plan.journeys[0].id
        mark_journey_evidence(
            plan,
            jid,
            evidence_type=EVIDENCE_MULTI_SESSION_E2E,
            notes="two contexts played match",
            passed=True,
        )
        self.assertTrue(plan.journeys[0].has_passing_evidence)
        self.assertTrue(plan.all_verified)


class CompletionGateTest(unittest.TestCase):
    def test_partial_when_journey_unverified(self):
        journeys = infer_critical_journeys(MULTIPLAYER)
        record = VerificationRecord(
            ran=True,
            passed=True,
            state=VerifyState.TESTS_PASSED,
            exit_code=0,
            tests_discovered=5,
            tests_passed=5,
            tests_failed=0,
            zero_tests=False,
        )
        # meaningful_pass needs relevant_preexisting empty and state ok
        checklist = TaskChecklist(
            task_id="t1",
            title="RPS",
            items=[
                RequirementItem(text="Two players can play a match", kind="product", status="Fully Addressed")
            ],
        )
        report = evaluate_completion(
            verification=record,
            checklist=checklist,
            journeys=journeys,
            unresolved_critical_nodes=0,
        )
        self.assertFalse(report.complete)
        self.assertTrue(report.partial)
        self.assertIn("PARTIAL", report.user_message)
        self.assertTrue(
            any(i.id == "critical_journeys" and not i.satisfied for i in report.items)
        )

    def test_complete_when_journeys_verified(self):
        journeys = infer_critical_journeys(MULTIPLAYER)
        for j in journeys.journeys:
            mark_journey_evidence(
                journeys,
                j.id,
                evidence_type=j.required_evidence_type,
                passed=True,
            )
        record = VerificationRecord(
            ran=True,
            passed=True,
            state=VerifyState.TESTS_PASSED,
            exit_code=0,
            tests_discovered=5,
            tests_passed=5,
            tests_failed=0,
            zero_tests=False,
        )
        checklist = TaskChecklist(
            task_id="t1",
            title="RPS",
            items=[
                RequirementItem(text="Two players can play a match", kind="product", status="Fully Addressed")
            ],
        )
        report = evaluate_completion(
            verification=record,
            checklist=checklist,
            journeys=journeys,
            unresolved_critical_nodes=0,
        )
        self.assertTrue(report.complete)


class PlanIntegrationTest(unittest.TestCase):
    def test_triage_fires_for_multiplayer(self):
        needed, reason, _ = triage_for_planning([], user_text=MULTIPLAYER)
        self.assertTrue(needed)
        self.assertTrue(
            "critical_journeys" in reason or "architecture_review" in reason
        )

    def test_draft_includes_capability_arch_journeys(self):
        plan = draft_plan_from_request(MULTIPLAYER, title="RPS multiplayer")
        self.assertIsNotNone(plan.capability_plan)
        self.assertTrue(plan.capability_plan.required)
        self.assertIsNotNone(plan.architecture)
        self.assertIsNotNone(plan.journeys)
        self.assertTrue(plan.journeys.journeys)
        self.assertTrue(
            any("weaken" in inv.lower() for inv in plan.invariants)
        )


if __name__ == "__main__":
    unittest.main()
