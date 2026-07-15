"""Tests for Z verify-before-commit gate and verification parsing."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_Z_HOME = tempfile.mkdtemp(prefix="z_gate_home_")
os.environ["Z_HOME"] = _Z_HOME
os.environ["Z_SKIP_VERIFY_GATE"] = ""  # ensure not skipped unless test sets it

from aider.z.uncertainty.gate import (  # noqa: E402
    classify_nodes,
    prepare_commit,
    record_acceptances,
)
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeStatus,
    NodeType,
    Tier,
    UncertaintyNode,
)
from aider.z.uncertainty.store import UncertaintyStore  # noqa: E402
from aider.z.uncertainty.verify import (  # noqa: E402
    VerificationRecord,
    VerifyState,
    detect_test_command,
    parse_discovery_count,
    path_to_importable,
)


class ParseDiscoveryTest(unittest.TestCase):
    def test_zero_collected_is_failure(self):
        count, zero = parse_discovery_count("collected 0 items\n")
        self.assertEqual(count, 0)
        self.assertTrue(zero)

    def test_collected_n(self):
        count, zero = parse_discovery_count("collected 12 items\n==== 12 passed")
        self.assertEqual(count, 12)
        self.assertFalse(zero)

    def test_unittest_ran_zero(self):
        count, zero = parse_discovery_count("Ran 0 tests in 0.001s\n")
        self.assertEqual(count, 0)
        self.assertTrue(zero)

    def test_passed_failed_sum(self):
        count, zero = parse_discovery_count("2 failed, 3 passed in 0.2s")
        self.assertEqual(count, 5)
        self.assertFalse(zero)


class DetectCommandTest(unittest.TestCase):
    def test_detects_pytest_ini(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            self.assertIn("-m pytest -q", detect_test_command(root))

    def test_detects_tests_dir_uses_unittest_when_no_pytest_declared(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_foo.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            cmd = detect_test_command(root)
            self.assertIn("-m unittest discover -s tests -v", cmd)

    def test_detects_pytest_when_declared(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requirements.txt").write_text("pytest>=7\n", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_foo.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
            self.assertIn("-m pytest -q", detect_test_command(root))


class PathImportTest(unittest.TestCase):
    def test_module_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(path_to_importable(root, "pkg/util.py"), "pkg.util")
            self.assertIsNone(path_to_importable(root, "tests/test_util.py"))


class ClassifyNodesTest(unittest.TestCase):
    def _node(self, **kwargs):
        defaults = dict(
            title="t",
            type=NodeType.API_ASSUMPTION,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.MEDIUM,
            summary="s",
        )
        defaults.update(kwargs)
        return UncertaintyNode(**defaults)

    def test_high_ignored_still_blocks(self):
        n = self._node(
            type=NodeType.MISSING_TEST,
            risk_tier=Tier.HIGH,
            status=NodeStatus.IGNORED,
            signals={"verification_blocked": True},
        )
        high, medium = classify_nodes([n])
        self.assertEqual(len(high), 1)
        self.assertEqual(medium, [])

    def test_high_resolved_clears(self):
        n = self._node(
            type=NodeType.MISSING_TEST,
            risk_tier=Tier.HIGH,
            status=NodeStatus.RESOLVED,
            signals={"verification_blocked": True},
        )
        high, medium = classify_nodes([n])
        self.assertEqual(high, [])

    def test_requirement_not_addressed_is_high(self):
        n = self._node(
            type=NodeType.REQUIREMENT_GAP,
            risk_tier=Tier.MEDIUM,
            signals={"requirement_status": "Not Addressed"},
        )
        high, medium = classify_nodes([n])
        self.assertEqual(len(high), 1)

    def test_requirement_partial_is_medium(self):
        n = self._node(
            type=NodeType.REQUIREMENT_GAP,
            risk_tier=Tier.MEDIUM,
            signals={"requirement_status": "Partially Addressed"},
        )
        high, medium = classify_nodes([n])
        self.assertEqual(high, [])
        self.assertEqual(len(medium), 1)

    def test_new_file_pattern_noise_does_not_medium_block(self):
        n = self._node(
            type=NodeType.NEW_FILE_NO_PATTERN,
            risk_tier=Tier.MEDIUM,
        )
        high, medium = classify_nodes([n])
        self.assertEqual(high, [])
        self.assertEqual(medium, [])

    def test_low_never_blocks(self):
        n = self._node(risk_tier=Tier.LOW, type=NodeType.TODO_COMMENT)
        high, medium = classify_nodes([n])
        self.assertEqual(high, [])
        self.assertEqual(medium, [])


class PrepareCommitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_app.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.store = UncertaintyStore(root=self.root, repo_key=str(self.root))
        self.engine = MagicMock()
        self.engine.ctx.current_task_id = "task-1"
        self.engine.ctx.current_task_title = "demo"
        self.engine.analyze_edits.return_value = []
        self.io = MagicMock()
        self.io.confirm_ask.return_value = False
        self.coder = MagicMock()
        self.coder.root = str(self.root)
        self.coder.uncertainty_engine = self.engine
        self.coder.uncertainty_store = self.store
        self.coder.verify_commit_gate = True
        self.coder.force_commit = False
        self.coder.verbose = False
        self.coder.test_cmd = "python -m pytest -q"
        self.coder.io = self.io
        self.coder._z_verify_gen_attempts = 0
        self.coder._z_verify_fix_attempts = 0
        self.coder.get_rel_fname = lambda p: p
        self.coder.partial_response_content = ""
        self.coder._ingest_uncertainty_self_reports = MagicMock()

    def tearDown(self):
        self.tmp.cleanup()

    def test_zero_tests_reflects_generate(self):
        record = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=0,
            tests_discovered=0,
            zero_tests=True,
            passed=False,
            state=VerifyState.NO_TESTS,
            output_excerpt="collected 0 items\n",
        )
        with patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(record, []),
        ):
            result = prepare_commit(self.coder, ["app.py"])
        self.assertFalse(result.allow_commit)
        self.assertIsNotNone(result.reflect_message)
        self.assertIn("Write a focused automated test", result.reflect_message)
        self.assertEqual(self.coder._z_verify_gen_attempts, 1)
        blocked = [n for n in self.store.list() if n.signals.get("verification_blocked")]
        self.assertTrue(blocked)
        self.assertEqual(blocked[0].risk_tier, Tier.HIGH)

    def test_failing_tests_reflect_fix(self):
        record = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=1,
            tests_discovered=2,
            zero_tests=False,
            passed=False,
            state=VerifyState.TESTS_FAILED,
            tests_failed=1,
            tests_passed=1,
            output_excerpt="1 failed, 1 passed\n",
        )
        with patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(record, ["tests/test_app.py"]),
        ):
            result = prepare_commit(self.coder, ["app.py"])
        self.assertFalse(result.allow_commit)
        self.assertIsNotNone(result.reflect_message)
        self.assertIn("test suite failed", result.reflect_message)
        self.assertEqual(self.coder._z_verify_fix_attempts, 1)

    def test_failed_suite_not_misreported_as_no_tests_when_relevant_empty(self):
        """Regression: '2 failed, 7 passed' must FIX, not GENERATE — even if relevant=[]."""
        record = VerificationRecord(
            ran=True,
            command="python -m unittest discover -s tests -v",
            exit_code=1,
            tests_discovered=9,
            zero_tests=False,
            passed=False,
            state=VerifyState.TESTS_FAILED,
            tests_failed=2,
            tests_passed=7,
            output_excerpt="2 failed, 7 passed in 0.4s\n",
        )
        with patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(record, []),  # empty relevant was the old bug trigger
        ):
            result = prepare_commit(self.coder, ["app.py"])
        self.assertFalse(result.allow_commit)
        self.assertIsNotNone(result.reflect_message)
        self.assertIn("test suite failed", result.reflect_message)
        self.assertNotIn("Write a focused automated test", result.reflect_message)
        self.assertEqual(self.coder._z_verify_fix_attempts, 1)
        self.assertEqual(self.coder._z_verify_gen_attempts, 0)
        self.assertTrue(self.coder._z_gate_hold_dirty)

    def test_meaningful_pass_allows_commit(self):
        record = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=0,
            tests_discovered=2,
            zero_tests=False,
            passed=True,
            state=VerifyState.TESTS_PASSED,
            smoke_ran=True,
            smoke_ok=True,
        )
        with patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(record, ["tests/test_app.py"]),
        ):
            result = prepare_commit(self.coder, ["app.py"])
        self.assertTrue(result.allow_commit)
        self.assertTrue(result.claimed_complete)
        self.engine.analyze_edits.assert_called()

    def test_medium_requires_explicit_ack(self):
        record = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=0,
            tests_discovered=2,
            zero_tests=False,
            passed=True,
            state=VerifyState.TESTS_PASSED,
        )
        medium_node = UncertaintyNode(
            title="Requirement gap: listing",
            type=NodeType.REQUIREMENT_GAP,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.MEDIUM,
            summary="partial",
            signals={"requirement_status": "Partially Addressed"},
        )
        self.store.add(medium_node)

        def _analyze(*a, **k):
            return [medium_node]

        self.engine.analyze_edits.side_effect = _analyze
        self.io.confirm_ask.return_value = False

        with patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(record, ["tests/test_app.py"]),
        ):
            result = prepare_commit(self.coder, ["app.py"])
        self.assertFalse(result.allow_commit)
        self.assertEqual(result.reason, "medium-risk not acknowledged")
        # explicit_yes_required must be used (yes-always cannot bypass)
        kwargs = self.io.confirm_ask.call_args.kwargs
        self.assertTrue(kwargs.get("explicit_yes_required"))

    def test_medium_ack_allows_and_logs(self):
        record = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=0,
            tests_discovered=2,
            zero_tests=False,
            passed=True,
            state=VerifyState.TESTS_PASSED,
        )
        medium_node = UncertaintyNode(
            title="API assumption",
            type=NodeType.API_ASSUMPTION,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.MEDIUM,
            summary="assumed",
        )
        self.store.add(medium_node)
        self.engine.analyze_edits.return_value = []
        self.io.confirm_ask.return_value = True

        with patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(record, ["tests/test_app.py"]),
        ):
            result = prepare_commit(self.coder, ["app.py"])
        self.assertTrue(result.allow_commit)
        self.assertTrue(medium_node.signals.get("gate_accepted"))
        self.assertEqual(medium_node.signals.get("gate_accepted_kind"), "medium_ack")

    def test_force_commit_overrides_high(self):
        record = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=1,
            tests_discovered=1,
            zero_tests=False,
            passed=False,
            state=VerifyState.TESTS_FAILED,
            output_excerpt="1 failed\n",
        )
        self.coder.force_commit = True

        with patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(record, ["tests/test_app.py"]),
        ):
            result = prepare_commit(self.coder, ["app.py"])
        self.assertTrue(result.allow_commit)
        self.assertTrue(result.force_override)
        self.assertIsNone(result.reflect_message)

    def test_high_block_without_force(self):
        record = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=1,
            tests_discovered=1,
            zero_tests=False,
            passed=False,
            state=VerifyState.TESTS_FAILED,
            output_excerpt="1 failed\n",
        )
        self.coder._z_verify_fix_attempts = 2
        self.io.confirm_ask.return_value = False

        with patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(record, ["tests/test_app.py"]),
        ):
            result = prepare_commit(self.coder, ["app.py"])
        self.assertFalse(result.allow_commit)
        self.assertTrue(result.blocked_high)

    def test_record_acceptances_sets_signals(self):
        n = UncertaintyNode(
            title="x",
            type=NodeType.API_ASSUMPTION,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.MEDIUM,
            summary="s",
        )
        self.store.add(n)
        record_acceptances(self.store, [n], "medium_ack", commit_hash="abc123")
        self.assertEqual(n.signals["gate_accepted_commit"], "abc123")


class MeaningfulPassTest(unittest.TestCase):
    def test_zero_not_meaningful(self):
        r = VerificationRecord(
            ran=True, exit_code=0, tests_discovered=0, zero_tests=True, passed=False
        )
        self.assertFalse(r.meaningful_pass)

    def test_pass_with_discovery(self):
        r = VerificationRecord(
            ran=True,
            exit_code=0,
            tests_discovered=3,
            zero_tests=False,
            passed=True,
            state=VerifyState.TESTS_PASSED,
        )
        self.assertTrue(r.meaningful_pass)


if __name__ == "__main__":
    unittest.main()
