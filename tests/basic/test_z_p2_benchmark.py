"""P2 benchmark unit/smoke tests — schema, scoring, harness (scripted adapter)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_p2_")
os.environ["Z_HOME"] = _HOME

from aider.z.benchmark.harness import (  # noqa: E402
    run_benchmark_issue,
    run_benchmark_suite,
)
from aider.z.benchmark.issues import (  # noqa: E402
    load_issues,
    summarize_task_type_counts,
)
from aider.z.benchmark.scoring import format_report, score_results  # noqa: E402


class IssueSetTests(unittest.TestCase):
    def test_issue_count_and_balance(self):
        issues = load_issues()
        self.assertGreaterEqual(len(issues), 25)
        self.assertLessEqual(len(issues), 50)
        counts = summarize_task_type_counts(issues)
        for t in (
            "diagnosis",
            "review",
            "bugfix",
            "feature",
            "migration",
            "refactor",
        ):
            self.assertGreaterEqual(
                counts.get(t, 0),
                4,
                msg=f"{t} under-represented: {counts}",
            )

    def test_trap_coverage(self):
        issues = load_issues()
        blob = " ".join(
            " ".join(i.known_traps) + " " + i.task_prompt for i in issues
        ).lower()
        self.assertIn("api", blob)
        # Specific P0/P1 failure-mode tags across the set
        traps = " ".join(" ".join(i.known_traps) for i in issues)
        self.assertTrue(
            any(x in traps for x in ("P0.2", "P0.3", "negative")),
            traps,
        )
        self.assertTrue("P1.1" in traps or "clause" in traps.lower(), traps)
        self.assertTrue("P0.5" in traps or "command" in traps.lower(), traps)
        self.assertTrue("P1.2" in traps or "node" in traps.lower(), traps)


class HarnessSmokeTests(unittest.TestCase):
    def test_diagnosis_full_vs_baseline(self):
        issues = load_issues(ids=["p2-001-diagnosis-average-api-trap"])
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        full = run_benchmark_issue(issue, uncertainty_enabled=True)
        base = run_benchmark_issue(issue, uncertainty_enabled=False)
        self.assertTrue(full.actually_complete, full)
        self.assertFalse(full.unnecessary_edits, full.edits)
        self.assertTrue(base.unnecessary_edits or not base.actually_complete, base)
        self.assertGreater(
            base.approval_interruptions, full.approval_interruptions
        )

    def test_bugfix_hidden_tests(self):
        issues = load_issues(ids=["p2-011-bugfix-average"])
        full = run_benchmark_issue(issues[0], uncertainty_enabled=True)
        self.assertTrue(full.hidden_tests_passed, full.hidden_test_details)
        self.assertTrue(full.actually_complete)
        self.assertTrue(full.root_cause_match)

    def test_migration_low_approvals(self):
        issues = load_issues(ids=["p2-022-migration-get-value"])
        full = run_benchmark_issue(issues[0], uncertainty_enabled=True)
        base = run_benchmark_issue(issues[0], uncertainty_enabled=False)
        self.assertTrue(full.actually_complete, full)
        self.assertLess(full.approval_interruptions, base.approval_interruptions)

    def test_node_resolution_issue(self):
        issues = load_issues(ids=["p2-013-bugfix-average-with-node"])
        full = run_benchmark_issue(issues[0], uncertainty_enabled=True)
        self.assertGreaterEqual(full.uncertainty_nodes_created, 1)
        self.assertGreaterEqual(full.uncertainty_nodes_resolved, 1)
        self.assertTrue(full.actually_complete)

    def test_suite_persist_and_score(self):
        with tempfile.TemporaryDirectory() as td:
            results = run_benchmark_suite(
                ids=[
                    "p2-001-diagnosis-average-api-trap",
                    "p2-011-bugfix-average",
                    "p2-017-feature-shout",
                    "p2-022-migration-get-value",
                    "p2-027-refactor-extract-swap",
                ],
                include_baseline=True,
                persist=True,
                results_dir=Path(td),
            )
            self.assertEqual(len(results), 10)  # 5 * 2
            report = score_results(results)
            text = format_report(report)
            self.assertIn("false_completion_rate", text)
            self.assertIn("issue_resolution_rate", text)
            # Full layer should beat baseline on resolution for this scripted set
            self.assertGreaterEqual(
                report.full.issue_resolution_rate,
                report.baseline.issue_resolution_rate,
            )
            self.assertLessEqual(
                report.full.false_completion_rate,
                report.baseline.false_completion_rate,
            )
            # Sanity: broken/disabled layer measurably worse on false completion
            # or unnecessary edits aggregate
            self.assertGreater(
                report.baseline.unnecessary_edit_rate
                + report.baseline.false_completion_rate,
                report.full.unnecessary_edit_rate
                + report.full.false_completion_rate,
            )
            meta = list(Path(td).glob("run-*.jsonl"))
            self.assertEqual(len(meta), 1)
            rows = [
                json.loads(line)
                for line in meta[0].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(rows), 10)


if __name__ == "__main__":
    unittest.main()
