"""ctest output must count as a real pass for the verification gate."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from aider.z.uncertainty.verify import (
    VerificationRecord,
    VerifyState,
    parse_counts,
    reconcile_ctest_pass,
    run_test_suite,
)


CTEST_GREEN = """\
Test project /Users/apple/fhx/build
    Start 1: event_bus_test
1/1 Test #1: event_bus_test ...................   Passed    0.04 sec

100% tests passed, 0 tests failed out of 1

Total Test time (real) =   0.04 sec
"""

CTEST_GREEN_NO_SUMMARY = """\
Test project /Users/apple/fhx/build
    Start 1: event_bus_test
1/1 Test #1: event_bus_test ...................   Passed    0.04 sec
"""

CTEST_RED = """\
Test project /tmp/build
    Start 1: event_bus_test
1/1 Test #1: event_bus_test ...................***Failed    0.02 sec

0% tests passed, 1 tests failed out of 1

Total Test time (real) =   0.02 sec
"""


class ParseCtestCountsTests(unittest.TestCase):
    def test_summary_line(self):
        c = parse_counts(CTEST_GREEN)
        self.assertEqual(c["discovered"], 1)
        self.assertEqual(c["passed"], 1)
        self.assertEqual(c["failed"], 0)
        self.assertFalse(c["zero"])

    def test_per_line_without_summary(self):
        c = parse_counts(CTEST_GREEN_NO_SUMMARY)
        self.assertEqual(c["discovered"], 1)
        self.assertEqual(c["passed"], 1)
        self.assertEqual(c["failed"], 0)

    def test_failed_suite(self):
        c = parse_counts(CTEST_RED)
        self.assertEqual(c["discovered"], 1)
        self.assertEqual(c["passed"], 0)
        self.assertEqual(c["failed"], 1)

    def test_pytest_still_works(self):
        c = parse_counts("===== 3 passed, 1 failed in 0.12s =====")
        self.assertEqual(c["passed"], 3)
        self.assertEqual(c["failed"], 1)
        self.assertEqual(c["discovered"], 4)


class ReconcileCtestPassTests(unittest.TestCase):
    def test_exit0_with_cmake_discovery(self):
        rec = VerificationRecord(
            ran=True,
            exit_code=0,
            passed=False,
            tests_discovered=None,
            tests_passed=None,
            tests_failed=None,
            error="Could not confirm tests were discovered from suite output",
            state=VerifyState.NO_TESTS,
        )
        # Simulate apply_cmake_result_to_record discovery overlay
        rec.tests_discovered = 1
        rec.zero_tests = False
        reconcile_ctest_pass(rec)
        self.assertTrue(rec.passed)
        self.assertEqual(rec.tests_passed, 1)
        self.assertEqual(rec.tests_failed, 0)
        self.assertEqual(rec.state, VerifyState.TESTS_PASSED)
        self.assertEqual(rec.error, "")


class RunTestSuiteCtestTests(unittest.TestCase):
    def test_green_ctest_marks_passed(self):
        from unittest.mock import patch

        with patch(
            "aider.z.uncertainty.verify.run_cmd",
            return_value=(0, CTEST_GREEN_NO_SUMMARY),
        ):
            rec = run_test_suite("/tmp", 'ctest --test-dir "/tmp/build"')
        self.assertEqual(rec.exit_code, 0)
        self.assertTrue(rec.passed)
        self.assertEqual(rec.tests_discovered, 1)
        self.assertEqual(rec.tests_passed, 1)
        self.assertEqual(rec.state, VerifyState.TESTS_PASSED)


class FormatFilesZThemeTests(unittest.TestCase):
    def test_vertical_paths_no_columns_glue(self):
        from aider.io import InputOutput

        io = InputOutput(pretty=True, fancy_input=False, z_theme=True, root="/tmp")
        out = io.format_files_for_input(
            [
                "CMakeLists.txt",
                "include/event_bus/event_bus.hpp",
                "tests/event_bus_test.cpp",
            ],
            [],
        )
        self.assertIn("CMakeLists.txt\n", out)
        self.assertIn("include/event_bus/event_bus.hpp\n", out)
        self.assertNotIn("event_bus.hppCMakeLists", out.replace("\n", ""))
        # One path per line — no space-joined column pack
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 3)


class PhaseSpinnerInterruptHintTests(unittest.TestCase):
    def test_start_adds_ctrl_c_hint(self):
        from unittest.mock import MagicMock, patch

        from aider.coders.base_coder import Coder
        from aider.z.mascot import MascotSpinner

        coder = MagicMock(spec=Coder)
        from aider.z.turn_ux import TurnOrchestrator

        orch = TurnOrchestrator()
        coder.io = SimpleNamespace(
            z_theme=True,
            tool_output=MagicMock(),
            agent_busy=False,
            _stop_agent_busy=None,
            turn_orchestrator=orch,
            ensure_turn_ux=MagicMock(return_value=orch),
            start_busy_queue_reader=MagicMock(),
            stop_busy_queue_reader=MagicMock(),
        )
        coder.waiting_spinner = None
        coder.show_pretty = lambda: True
        coder._stop_waiting_spinner = Coder._stop_waiting_spinner.__get__(coder)
        coder._phase_spinner_start = Coder._phase_spinner_start.__get__(coder)
        coder._phase_spinner_stop = Coder._phase_spinner_stop.__get__(coder)

        fake = MagicMock(spec=MascotSpinner)
        with patch("aider.coders.base_coder.waiting_display", return_value=fake) as wd:
            with patch("sys.stdout.write"), patch("sys.stdout.flush"):
                coder._phase_spinner_start("Planning — building capability plan…")
        args = wd.call_args[0][0]
        self.assertIn("Ctrl+C to interrupt", args)
        self.assertTrue(coder.io.agent_busy)
        coder._phase_spinner_stop()
        self.assertFalse(coder.io.agent_busy)


if __name__ == "__main__":
    unittest.main()
