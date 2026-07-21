"""P1 reliability: clean-room, backtrack, UX, assertions, browser, artifacts, benchmark."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_rel9p1_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.artifacts import is_agent_artifact, scan_artifacts  # noqa: E402
from aider.z.uncertainty.assertions import (  # noqa: E402
    generate_transition_tests,
    infer_transition_table,
    scan_weak_assertions,
)
from aider.z.uncertainty.backtrack import backtrack_failure  # noqa: E402
from aider.z.uncertainty.benchmark import (  # noqa: E402
    BENCHMARK_TASKS,
    aggregate_false_completion_rate,
    score_task,
)
from aider.z.uncertainty.browser_sessions import (  # noqa: E402
    draft_multi_session_plan,
    run_multi_session,
)
from aider.z.uncertainty.cleanroom import discover_cleanroom_plan  # noqa: E402
from aider.z.uncertainty.completion import evaluate_completion  # noqa: E402
from aider.z.uncertainty.evidence import EvidenceLedger, EvidenceRecord, tree_hash  # noqa: E402
from aider.z.uncertainty.journeys import (  # noqa: E402
    EVIDENCE_MULTI_SESSION_E2E,
    infer_critical_journeys,
    mark_journey_evidence,
)
from aider.z.uncertainty.plan import draft_plan_from_request  # noqa: E402
from aider.z.uncertainty.ux_states import draft_ux_model  # noqa: E402
from aider.z.uncertainty.verify import VerificationRecord, VerifyState  # noqa: E402


MULTIPLAYER = (
    "Build a multiplayer rock-paper-scissors web app with Next.js. "
    "Two players join a lobby via QR code, challenge each other, "
    "play best-of-three with hidden choices, and return to the lobby."
)


class EvidenceLedgerTest(unittest.TestCase):
    def test_edits_invalidate(self):
        ledger = EvidenceLedger()
        ledger.add(
            EvidenceRecord(
                kind="typecheck",
                command="tsc",
                cwd=".",
                exit_code=0,
                passed=True,
                tree_hash_at_run="aaa",
            )
        )
        stale = ledger.invalidate_after_edits(
            current_tree_hash="bbb", edited=["src/a.ts"]
        )
        self.assertEqual(len(stale), 1)
        self.assertTrue(ledger.records[0].stale)
        self.assertFalse(ledger.fresh_pass("typecheck"))

    def test_tree_hash_stable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("x=1\n", encoding="utf-8")
            h1 = tree_hash(root, paths=["a.py"])
            h2 = tree_hash(root, paths=["a.py"])
            self.assertEqual(h1, h2)
            (root / "a.py").write_text("x=2\n", encoding="utf-8")
            h3 = tree_hash(root, paths=["a.py"])
            self.assertNotEqual(h1, h3)


class CleanRoomTest(unittest.TestCase):
    def test_discovers_npm_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(
                '{"scripts":{"typecheck":"tsc --noEmit","test":"jest","build":"next build","start":"next start"}}',
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text("{}", encoding="utf-8")
            plan = discover_cleanroom_plan(root)
            kinds = [s.kind for s in plan.steps if s.command]
            self.assertIn("clean_install", kinds)
            self.assertIn("typecheck", kinds)
            self.assertIn("build", kinds)
            install = next(s for s in plan.steps if s.kind == "clean_install")
            self.assertIn("npm ci", install.command)


class BacktrackTest(unittest.TestCase):
    def test_command_not_found_earliest_is_env(self):
        bt = backtrack_failure(
            output="sh: tsc: command not found",
            command="npm run typecheck",
            exit_code=127,
            failure_kind="typecheck",
        )
        self.assertEqual(bt.classification.layer, "command_not_found")
        self.assertIsNotNone(bt.earliest)
        self.assertIn(bt.earliest.id, ("env_ready", "deps_installed"))
        self.assertIn("Repair", bt.repair_guidance)

    def test_weaken_blocked_flag(self):
        bt = backtrack_failure(
            output="error TS2339",
            failure_kind="typecheck",
            proposed_repair_touches_detector=True,
        )
        self.assertTrue(bt.weaken_blocked)
        self.assertIn("BLOCKED", bt.repair_guidance)


class UxModelTest(unittest.TestCase):
    def test_multiplayer_states(self):
        model = draft_ux_model(MULTIPLAYER)
        self.assertTrue(model.applicable)
        ids = {s.id for s in model.states}
        self.assertIn("lobby", ids)
        self.assertIn("selecting_move", ids)
        self.assertTrue(any(v.id == "multi_user_sync" for v in model.verification))


class AssertionTest(unittest.TestCase):
    def test_flags_or_tobetruthy(self):
        findings = scan_weak_assertions(
            {
                "tests/game.test.ts": (
                    'expect(result === "round_win" || result === "win").toBeTruthy();\n'
                )
            }
        )
        self.assertTrue(findings)
        self.assertEqual(findings[0].kind, "alternative_or")

    def test_transition_table_and_codegen(self):
        table = infer_transition_table(MULTIPLAYER)
        self.assertIsNotNone(table)
        code = generate_transition_tests(table)
        self.assertIn("toEqual", code)
        self.assertIn("Challenge pending", code)


class BrowserSessionTest(unittest.TestCase):
    def test_plan_two_contexts(self):
        plan = draft_multi_session_plan(MULTIPLAYER)
        self.assertTrue(plan.required)
        self.assertEqual(len(plan.contexts), 2)

    def test_unavailable_keeps_journey_unverified(self):
        os.environ["Z_BROWSER_TOOL"] = "none"
        try:
            plan = draft_multi_session_plan(MULTIPLAYER)
            journeys = infer_critical_journeys(MULTIPLAYER)
            res = run_multi_session(plan, journeys=journeys)
            self.assertFalse(res.tools_available)
            self.assertFalse(res.passed)
            self.assertIn("unverified", res.detail.lower())
            self.assertFalse(journeys.all_verified)
        finally:
            os.environ.pop("Z_BROWSER_TOOL", None)


class ArtifactTest(unittest.TestCase):
    def test_flags_z_and_history(self):
        self.assertTrue(is_agent_artifact(".z/uncertainty/foo.json"))
        self.assertTrue(is_agent_artifact("aider.chat.history.md"))
        self.assertFalse(is_agent_artifact("src/app.ts"))
        report = scan_artifacts([".z/cache", "src/ok.py", "tmp_debug.json"])
        self.assertFalse(report.clean)
        self.assertTrue(any(".z" in p or "tmp_debug" in p for p in report.paths))


class BenchmarkTest(unittest.TestCase):
    def test_catalog_covers_taxonomy(self):
        cats = {t.category for t in BENCHMARK_TASKS}
        self.assertIn("multi_user_realtime", cats)
        self.assertIn("stop_and_ask", cats)
        self.assertIn("dependency_failures", cats)

    def test_false_completion_scored(self):
        task = next(t for t in BENCHMARK_TASKS if t.id == "multi_user")
        bad = score_task(
            task,
            claimed_complete=True,
            journeys_verified=False,
            verification_weakened=False,
            functional_ok=True,
        )
        self.assertTrue(bad.false_completion)
        good = score_task(
            task,
            claimed_complete=True,
            journeys_verified=True,
            verification_weakened=False,
            functional_ok=True,
        )
        self.assertFalse(good.false_completion)
        rate = aggregate_false_completion_rate([bad, good])
        self.assertEqual(rate, 0.5)

    def test_stop_and_ask(self):
        task = next(t for t in BENCHMARK_TASKS if t.id == "stop_and_ask")
        s = score_task(
            task,
            claimed_complete=True,
            journeys_verified=True,
            verification_weakened=False,
            functional_ok=False,
            asked_for_help=False,
        )
        self.assertTrue(s.false_completion)


class PlanP1IntegrationTest(unittest.TestCase):
    def test_draft_includes_ux_transitions_multisession(self):
        plan = draft_plan_from_request(MULTIPLAYER, title="RPS")
        self.assertIsNotNone(plan.ux_model)
        self.assertIsNotNone(plan.transition_table)
        self.assertIsNotNone(plan.multi_session_plan)

    def test_completion_partial_with_multi_session(self):
        journeys = infer_critical_journeys(MULTIPLAYER)
        record = VerificationRecord(
            ran=True,
            passed=True,
            state=VerifyState.TESTS_PASSED,
            exit_code=0,
            tests_discovered=3,
            tests_passed=3,
            tests_failed=0,
            zero_tests=False,
        )
        report = evaluate_completion(
            verification=record,
            journeys=journeys,
            multi_session_required=True,
            multi_session_verified=False,
            unresolved_critical_nodes=0,
        )
        self.assertFalse(report.complete)
        self.assertTrue(
            any(i.id == "multi_session" and not i.satisfied for i in report.items)
        )


if __name__ == "__main__":
    unittest.main()
