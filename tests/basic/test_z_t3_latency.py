"""T3-inspired latency helpers — overlap without weakening Z cores."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class LatencyOverlapTests(unittest.TestCase):
    def setUp(self):
        os.environ["Z_LATENCY_OVERLAP"] = "1"
        os.environ["Z_EXPLORE_PASS"] = "1"

    def test_submit_and_join(self):
        from aider.z.latency import join_future, submit_background

        fut = submit_background(lambda: "ok")
        self.assertEqual(join_future(fut, timeout=2.0), "ok")

    def test_join_timeout_returns_none(self):
        from aider.z.latency import join_future, submit_background

        fut = submit_background(time.sleep, 5)
        self.assertIsNone(join_future(fut, timeout=0.2))

    def test_explore_overlaps_checklist(self):
        """Explore starts before checklist finishes when overlap is on."""
        from aider.coders.base_coder import Coder
        from aider.z.task_mode import TaskMode

        order: list[str] = []

        coder = MagicMock(spec=Coder)
        coder.io = MagicMock()
        coder.task_mode = TaskMode.IMPLEMENT
        coder.abs_fnames = set()
        coder.cur_messages = []
        coder.root = "."
        coder.verbose = False

        coder._explore_pass_eligible = Coder._explore_pass_eligible.__get__(coder)
        coder._compute_explore_block = lambda msg: (order.append("explore"), "# scout\n")[1]
        coder._inject_explore_block = Coder._inject_explore_block.__get__(coder)
        coder._start_explore_pass_async = Coder._start_explore_pass_async.__get__(coder)
        coder._finish_explore_pass = Coder._finish_explore_pass.__get__(coder)
        coder._cancel_explore_pass = Coder._cancel_explore_pass.__get__(coder)
        coder._maybe_explore_pass = Coder._maybe_explore_pass.__get__(coder)

        with patch("aider.z.explore.explore_pass_enabled", return_value=True):
            fut = coder._start_explore_pass_async("implement a thread-safe event bus now")
            order.append("checklist")
            coder._finish_explore_pass(fut)

        self.assertIn("explore", order)
        self.assertIn("checklist", order)
        # Background explore should have injected into cur_messages
        self.assertTrue(
            any("scout" in (m.get("content") or "") for m in coder.cur_messages),
            coder.cur_messages,
        )


class ExploreBudgetTests(unittest.TestCase):
    def test_rg_skips_filename_walk(self):
        from unittest import mock

        from aider.z import explore as explore_mod

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "bus.cpp").write_text("void publish() {}\n", encoding="utf-8")
            walked = []

            def boom(*a, **k):
                walked.append(True)
                return []

            with mock.patch.object(explore_mod, "_rg_available", return_value=True):
                with mock.patch.object(
                    explore_mod,
                    "_search_rg",
                    return_value=[("bus.cpp", "publish")],
                ):
                    with mock.patch.object(
                        explore_mod, "_search_path_names", side_effect=boom
                    ):
                        explore_mod._rank_candidates(
                            "implement event bus publish",
                            root,
                            already_in_chat=[],
                            max_keywords=4,
                            max_files=6,
                        )
            self.assertEqual(walked, [])


class RelevantFirstVerifyTests(unittest.TestCase):
    def setUp(self):
        os.environ["Z_VERIFY_RELEVANT_FIRST"] = "1"

    def test_relevant_fail_skips_broad_suite(self):
        from aider.z.uncertainty import verify as verify_mod
        from aider.z.uncertainty.verify import VerifyState, verify_edits

        calls: list[str] = []

        def fake_suite(root, cmd, **kwargs):
            calls.append(cmd if isinstance(cmd, str) else str(cmd))
            from aider.z.uncertainty.verify import VerificationRecord

            # First call is relevant-targeted (path in cmd); fail it
            is_relevant = "test_bus" in str(cmd) or (
                isinstance(cmd, (list, tuple)) and any("test_bus" in str(x) for x in cmd)
            )
            if not calls or is_relevant or len(calls) == 1:
                return VerificationRecord(
                    ran=True,
                    command=str(cmd),
                    exit_code=1,
                    passed=False,
                    state=VerifyState.TESTS_FAILED,
                    tests_discovered=1,
                    tests_failed=1,
                    output_excerpt="FAILED test_bus",
                )
            return VerificationRecord(
                ran=True,
                command=str(cmd),
                exit_code=0,
                passed=True,
                state=VerifyState.TESTS_PASSED,
                tests_discovered=10,
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "bus.py").write_text("def publish():\n    pass\n", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_bus.py").write_text(
                "def test_publish():\n    assert True\n", encoding="utf-8"
            )

            with patch.object(verify_mod, "find_relevant_tests", return_value=["tests/test_bus.py"]):
                with patch.object(
                    verify_mod,
                    "classify_relevant_tests",
                    return_value=(["tests/test_bus.py"], []),
                ):
                    with patch.object(
                        verify_mod,
                        "detect_test_command",
                        return_value="python -m pytest",
                    ):
                        with patch.object(
                            verify_mod,
                            "build_relevant_test_command",
                            return_value="python -m pytest tests/test_bus.py",
                        ):
                            with patch.object(verify_mod, "run_test_suite", side_effect=fake_suite):
                                record, _ = verify_edits(
                                    root,
                                    ["bus.py"],
                                    skip_smoke=True,
                                    skip_package_prechecks=True,
                                    skip_type_members=True,
                                )

        self.assertEqual(record.failure_kind, "relevant_tests")
        self.assertFalse(record.passed)
        # Broad suite must not have run after relevant failure
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("test_bus", calls[0])


if __name__ == "__main__":
    unittest.main()
