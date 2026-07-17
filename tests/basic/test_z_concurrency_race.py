"""Concurrency-relevant tagging + race-detector before/after verification."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_concurrency_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.concurrency_checks import (  # noqa: E402
    RaceComparison,
    RaceRunResult,
    RaceTool,
    analyze_concurrency_change,
    classify_race_outcome,
    concurrency_nodes_from_comparison,
    discover_race_tools,
    parse_race_count,
    tag_concurrency_relevant,
)
from aider.z.uncertainty.gate import (  # noqa: E402
    _effective_gate_tier,
    _reflect_fix_races,
    classify_nodes,
)
from aider.z.uncertainty.risk import DetectionSignals, derive_confidence_tier  # noqa: E402
from aider.z.uncertainty.schema import NodeType, Tier  # noqa: E402
from aider.z.uncertainty.verify import VerificationRecord, VerifyState  # noqa: E402


_ATOMIC_DIFF = (
    "diff --git a/src/queue.hpp b/src/queue.hpp\n"
    "--- a/src/queue.hpp\n"
    "+++ b/src/queue.hpp\n"
    "@@ -10,7 +10,8 @@\n"
    "-    volatile uint32_t size;\n"
    "+    std::atomic<uint32_t> size;\n"
    "+    // publish with memory_order_release\n"
    "+    size.store(n, std::memory_order_release);\n"
)


class TaggingTest(unittest.TestCase):
    def test_tags_atomic_memory_order_diff(self):
        tag = tag_concurrency_relevant(_ATOMIC_DIFF, ["src/queue.hpp"])
        self.assertTrue(tag.relevant)
        blob = " ".join(tag.reasons).lower()
        self.assertTrue(
            "atomic" in blob or "memory_order" in blob or "volatile" in blob,
            tag.reasons,
        )

    def test_tags_go_sync_primitives(self):
        diff = (
            "diff --git a/pkg/w.go b/pkg/w.go\n"
            "--- a/pkg/w.go\n"
            "+++ b/pkg/w.go\n"
            "@@ -1,2 +1,5 @@\n"
            "+var mu sync.Mutex\n"
            "+func (w *W) Do() { mu.Lock(); defer mu.Unlock() }\n"
        )
        tag = tag_concurrency_relevant(diff, ["pkg/w.go"])
        self.assertTrue(tag.relevant)

    def test_quiet_on_unrelated_diff(self):
        diff = (
            "diff --git a/util.py b/util.py\n"
            "--- a/util.py\n"
            "+++ b/util.py\n"
            "@@ -1,2 +1,3 @@\n"
            "+def add(a, b): return a + b\n"
        )
        tag = tag_concurrency_relevant(diff, ["util.py"])
        self.assertFalse(tag.relevant)

    def test_path_hint_alone_can_tag(self):
        tag = tag_concurrency_relevant("", ["src/lockfree_queue.cpp"])
        self.assertTrue(tag.relevant)


class ParseRaceCountTest(unittest.TestCase):
    def test_counts_tsan_warnings(self):
        out = (
            "WARNING: ThreadSanitizer: data race\n"
            "  #0 foo\n"
            "WARNING: ThreadSanitizer: data race\n"
            "  #0 bar\n"
        )
        self.assertEqual(parse_race_count(out), 2)

    def test_counts_go_data_race(self):
        out = "WARNING: DATA RACE\nRead at 0x1\nWARNING: DATA RACE\nWrite at 0x2\n"
        self.assertEqual(parse_race_count(out), 2)

    def test_zero_when_clean(self):
        self.assertEqual(parse_race_count("ok\nPASS\n"), 0)


class OutcomeClassificationTest(unittest.TestCase):
    def _run(self, before_n, after_n):
        before = RaceRunResult(ran=True, race_count=before_n, phase="before")
        after = RaceRunResult(ran=True, race_count=after_n, phase="after")
        return classify_race_outcome(before, after)

    def test_clean_reduction_to_zero(self):
        outcome, summary = self._run(11, 0)
        self.assertEqual(outcome, "clean")
        self.assertIn("11", summary)

    def test_reduced_but_not_cleared(self):
        outcome, summary = self._run(11, 2)
        self.assertEqual(outcome, "reduced")
        self.assertTrue(RaceComparison(outcome="reduced").soft_block)
        self.assertFalse(RaceComparison(outcome="reduced").blocks_commit)

    def test_no_improvement_blocks(self):
        outcome, _ = self._run(2, 2)
        self.assertEqual(outcome, "no_improvement")
        self.assertTrue(RaceComparison(outcome="no_improvement").blocks_commit)

    def test_regression_blocks(self):
        outcome, _ = self._run(2, 5)
        self.assertEqual(outcome, "regression")
        self.assertTrue(RaceComparison(outcome="regression").blocks_commit)

    def test_after_only_soft(self):
        after = RaceRunResult(ran=True, race_count=0, phase="after")
        outcome, summary = classify_race_outcome(None, after)
        self.assertEqual(outcome, "after_only")
        self.assertIn("non-deterministic", summary.lower())
        self.assertTrue(RaceComparison(outcome="after_only").soft_block)


class DiscoverToolsTest(unittest.TestCase):
    def test_discovers_go_race(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
            (root / "x.go").write_text("package x\n", encoding="utf-8")
            with mock.patch(
                "aider.z.uncertainty.concurrency_checks.shutil.which",
                return_value="/usr/bin/go",
            ):
                tools = discover_race_tools(root, ["x.go"])
            self.assertTrue(any(t.tool_id == "go_race" for t in tools), tools)

    def test_discovers_npm_test_race(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(
                '{"scripts": {"test:race": "node --test --experimental-test-coverage"}}',
                encoding="utf-8",
            )
            tools = discover_race_tools(root, ["index.js"])
            self.assertTrue(any("race" in t.tool_id for t in tools), tools)

    def test_env_race_cmd(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = os.environ.get("Z_RACE_DETECT_CMD")
            os.environ["Z_RACE_DETECT_CMD"] = "./scripts/tsan.sh"
            try:
                tools = discover_race_tools(root, ["src/queue.hpp"])
            finally:
                if old is None:
                    os.environ.pop("Z_RACE_DETECT_CMD", None)
                else:
                    os.environ["Z_RACE_DETECT_CMD"] = old
            self.assertTrue(any(t.tool_id == "env_race_cmd" for t in tools), tools)


class AnalyzeAndNodesTest(unittest.TestCase):
    def test_tool_missing_soft_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "queue.hpp").write_text(
                "std::atomic<int> x;\n", encoding="utf-8"
            )
            old = os.environ.pop("Z_RACE_DETECT_CMD", None)
            try:
                cmp_ = analyze_concurrency_change(
                    root,
                    diff=_ATOMIC_DIFF,
                    edited=["src/queue.hpp"],
                )
            finally:
                if old is not None:
                    os.environ["Z_RACE_DETECT_CMD"] = old
            self.assertTrue(cmp_.concurrency_relevant)
            self.assertEqual(cmp_.outcome, "tool_missing")
            self.assertTrue(cmp_.soft_block)
            self.assertFalse(cmp_.blocks_commit)

            sig = DetectionSignals(files_changed=["src/queue.hpp"])
            nodes = concurrency_nodes_from_comparison(
                cmp_, signals=sig, files=["src/queue.hpp"]
            )
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].type, NodeType.CONCURRENCY_RACE)
            self.assertEqual(nodes[0].risk_tier, Tier.MEDIUM)
            # Soft-blocks via medium gate types
            self.assertEqual(_effective_gate_tier(nodes[0]), Tier.MEDIUM)
            high, medium = classify_nodes(nodes)
            self.assertEqual(len(high), 0)
            self.assertEqual(len(medium), 1)

    def test_no_improvement_hard_block_node(self):
        cmp_ = RaceComparison(
            concurrency_relevant=True,
            tool=RaceTool("go_race", "go test -race", "go test -race ./...", "go"),
            tool_available=True,
            before=RaceRunResult(ran=True, race_count=2, phase="before"),
            after=RaceRunResult(ran=True, race_count=2, phase="after"),
            outcome="no_improvement",
            summary="Before/after: 2 → 2 races",
        )
        self.assertTrue(cmp_.blocks_commit)
        sig = DetectionSignals(files_changed=["q.go"])
        nodes = concurrency_nodes_from_comparison(cmp_, signals=sig, files=["q.go"])
        self.assertEqual(nodes[0].risk_tier, Tier.HIGH)
        self.assertEqual(_effective_gate_tier(nodes[0]), Tier.HIGH)

    def test_clean_never_high_confidence(self):
        cmp_ = RaceComparison(
            concurrency_relevant=True,
            tool=RaceTool("env", "tsan", "./tsan.sh", "c++"),
            tool_available=True,
            before=RaceRunResult(ran=True, race_count=11, phase="before"),
            after=RaceRunResult(ran=True, race_count=0, phase="after"),
            outcome="clean",
            summary="11 → 0",
        )
        sig = DetectionSignals(
            files_changed=["q.hpp"],
            tests_relevant_exist=True,
            tests_passed=True,
            concurrency_relevant=True,
            race_detector_ran=True,
            race_detector_outcome="clean",
        )
        nodes = concurrency_nodes_from_comparison(cmp_, signals=sig, files=["q.hpp"])
        self.assertEqual(nodes[0].risk_tier, Tier.LOW)
        conf = derive_confidence_tier(sig, NodeType.CONCURRENCY_RACE)
        self.assertNotEqual(conf, Tier.HIGH)
        self.assertIn("non-deterministic", nodes[0].why_uncertain.lower())

    def test_before_after_mocked_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Minimal git repo so before-pass can restore HEAD
            os.system(f"git -C {td} init -q")
            os.system(f"git -C {td} config user.email t@t.com")
            os.system(f"git -C {td} config user.name t")
            src = root / "race.go"
            src.write_text("package main\n", encoding="utf-8")
            os.system(f"git -C {td} add race.go && git -C {td} commit -q -m init")
            src.write_text(
                "package main\n// std::atomic fake for tag via path\n",
                encoding="utf-8",
            )
            (root / "go.mod").write_text("module example.com/r\n", encoding="utf-8")

            calls = {"n": 0}

            def fake_run(cmd, **kwargs):
                calls["n"] += 1
                # First call is "after" (fixed), second is "before" (HEAD)
                if calls["n"] == 1:
                    return 0, "ok\n"  # after: 0 races
                return 1, (
                    "WARNING: DATA RACE\n"
                    "WARNING: DATA RACE\n"
                    "WARNING: DATA RACE\n"
                )

            old = os.environ.get("Z_RACE_DETECT_CMD")
            os.environ["Z_RACE_DETECT_CMD"] = "echo race-detect"
            try:
                with mock.patch(
                    "aider.z.uncertainty.concurrency_checks.run_cmd",
                    side_effect=fake_run,
                ), mock.patch(
                    "aider.z.uncertainty.concurrency_checks.shutil.which",
                    return_value="/usr/bin/go",
                ):
                    # Prefer env cmd so we control invocation
                    cmp_ = analyze_concurrency_change(
                        root,
                        diff=(
                            "diff --git a/race.go b/race.go\n"
                            "--- a/race.go\n"
                            "+++ b/race.go\n"
                            "@@ -1 +1,2 @@\n"
                            "+var mu sync.Mutex\n"
                        ),
                        edited=["race.go"],
                    )
            finally:
                if old is None:
                    os.environ.pop("Z_RACE_DETECT_CMD", None)
                else:
                    os.environ["Z_RACE_DETECT_CMD"] = old

            self.assertEqual(cmp_.outcome, "clean")
            self.assertEqual(cmp_.after.race_count, 0)
            self.assertEqual(cmp_.before.race_count, 3)


class VerifyMeaningfulPassTest(unittest.TestCase):
    def test_race_detected_not_meaningful_pass(self):
        rec = VerificationRecord(
            ran=True,
            state=VerifyState.RACE_DETECTED,
            failure_kind="race_detection",
            passed=False,
            exit_code=1,
            tests_discovered=5,
            zero_tests=False,
        )
        self.assertFalse(rec.meaningful_pass)

    def test_reflect_mentions_before_after(self):
        rec = VerificationRecord(
            state=VerifyState.RACE_DETECTED,
            failure_kind="race_detection",
            race_comparison={
                "outcome": "no_improvement",
                "before_races": 2,
                "after_races": 2,
                "tool_id": "go_race",
                "summary": "2 → 2",
            },
            output_excerpt="WARNING: DATA RACE\n",
        )
        msg = _reflect_fix_races(rec, ["q.go"])
        self.assertIn("RACE DETECTOR", msg)
        self.assertIn("before/after", msg.lower())
        self.assertIn("non-deterministic", msg.lower())


if __name__ == "__main__":
    unittest.main()
