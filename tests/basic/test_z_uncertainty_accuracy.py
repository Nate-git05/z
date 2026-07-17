"""Accuracy hardening: path collision, requirement-gap signal wiring, kinds/evidence."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_unc_acc_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.checklist import (  # noqa: E402
    bind_evidence,
    classify_requirement_kind,
    ledger_snapshot,
    rescore_checklist_with_evidence,
)
from aider.z.uncertainty.context import apply_uncertainty_budget  # noqa: E402
from aider.z.uncertainty.detectors import (  # noqa: E402
    detect_missing_or_failing_tests,
    detect_requirement_gaps,
    reconcile_requirement_with_signals,
)
from aider.z.uncertainty.outcomes import (  # noqa: E402
    detector_circuit_open,
    record_outcome,
    reset_outcomes,
    resolution_rate,
)
from aider.z.uncertainty.risk import DetectionSignals, collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
    UncertaintyNode,
)
from aider.z.uncertainty.store import (  # noqa: E402
    UncertaintyStore,
    local_store_filename,
)
from aider.z.uncertainty.verify import VerificationRecord, VerifyState  # noqa: E402


class StorePathCollisionTest(unittest.TestCase):
    def test_long_prefix_paths_get_distinct_filenames(self):
        # Differ only past character 80 of the sanitized key
        a = "/very/long/common/prefix/" + ("x" * 100) + "/repo_a"
        b = "/very/long/common/prefix/" + ("x" * 100) + "/repo_b"
        fa = local_store_filename(a)
        fb = local_store_filename(b)
        self.assertNotEqual(fa, fb)
        # Hash suffix present
        self.assertIn("_", fa)
        digest_a = hashlib.sha256(a.encode()).hexdigest()[:12]
        self.assertTrue(fa.endswith(f"_{digest_a}.json"))

    def test_load_rejects_repo_key_mismatch(self):
        store_a = UncertaintyStore(repo_key="path/alpha")
        from aider.z.uncertainty.schema import NodeStatus

        node = UncertaintyNode(
            title="n1",
            type=NodeType.TODO_COMMENT,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.LOW,
            summary="s",
            status=NodeStatus.OPEN,
        )
        store_a.add(node, sync=False)
        path = store_a._local_path
        self.assertTrue(path.is_file())

        # Force same filename with different repo_key by rewriting JSON key
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        data["repo_key"] = "path/other"
        path.write_text(json.dumps(data), encoding="utf-8")

        store_b = UncertaintyStore(repo_key="path/alpha")
        # Mismatch → start fresh, do not merge foreign nodes
        store_b._local_path = path
        store_b.nodes = {}
        store_b.load_local()
        self.assertEqual(store_b.nodes, {})


class ClassificationTest(unittest.TestCase):
    def test_process_vs_product_examples(self):
        cases = {
            "Implement allow(key)": "product",
            "Thread-safe under contention": "quality",
            "Document semantics in README": "documentation",
            "Run the tests": "verification",
            "Run the complete test suite": "verification",
            "Do not commit until the verified working tree passes": "process",
            "Do not commit before tests pass": "process",
            "Fix failures before finishing": "process",
            "Which Markdown anchor format?": "decision",
            "Production API supports this field": "external_assumption",
            "Use uncertainty before committing": "process",
            "Also investigate a vector-reallocation race in bgLogInfos": "investigation",
            "Look at registerLogInfo for concurrent growth": "investigation",
        }
        for text, kind in cases.items():
            self.assertEqual(
                classify_requirement_kind(text),
                kind,
                msg=f"{text!r} → expected {kind}",
            )


class RequirementGapSignalWiringTest(unittest.TestCase):
    def test_run_the_tests_plus_tests_passed_emits_no_node(self):
        """Exact Claude Fix 1 repro: checklist 'Run the tests' + tests_passed → no gap."""
        checklist = TaskChecklist(
            task_id="t1",
            title="Rate limiter",
            items=[
                RequirementItem(
                    text="Run the tests",
                    status="Not Addressed",
                    kind="verification",
                )
            ],
        )
        sig = collect_base_signals(["flowguard/rate_limiter.py"])
        sig.tests_passed = True
        nodes = detect_requirement_gaps(sig, checklist=checklist)
        self.assertEqual(nodes, [])
        self.assertEqual(checklist.items[0].status, "Fully Addressed")

    def test_process_commit_rule_satisfied_by_verification(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="Task",
            items=[
                RequirementItem(
                    text="Do not commit until the verified working tree passes",
                    status="Not Addressed",
                    kind="process",
                ),
                RequirementItem(
                    text="Fix failures before finishing",
                    status="Not Addressed",
                    kind="process",
                ),
            ],
        )
        record = VerificationRecord(
            ran=True,
            exit_code=0,
            tests_discovered=7,
            tests_passed=7,
            tests_failed=0,
            passed=True,
            state=VerifyState.TESTS_PASSED,
        )
        evidence = bind_evidence(
            checklist,
            files_changed=["flowguard/rate_limiter.py"],
            file_contents={
                "flowguard/rate_limiter.py": "def allow(key): pass\ndef prune(): pass\n"
            },
            symbols=["allow", "prune"],
            test_files=["tests/test_flowguard.py"],
            execution_log="Verification state=TESTS_PASSED exit=0",
            verification=record,
        )
        rescore_checklist_with_evidence(checklist, evidence)
        for item in checklist.items:
            self.assertEqual(item.status, "Fully Addressed", msg=item.text)

        sig = collect_base_signals(["flowguard/rate_limiter.py"])
        sig.tests_passed = True
        nodes = detect_requirement_gaps(sig, checklist=checklist)
        self.assertEqual(nodes, [])

    def test_documentation_satisfied_when_readme_edited(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="Docs",
            items=[
                RequirementItem(
                    text="Document allow() semantics in README",
                    status="Not Addressed",
                    kind="documentation",
                )
            ],
        )
        # Pre-existing README alone must NOT satisfy — docs must be touched
        evidence_untouched = bind_evidence(
            checklist,
            files_changed=["flowguard/rate_limiter.py"],
            file_contents={
                "flowguard/rate_limiter.py": "def allow(key):\n    return True\n",
                "README.md": "# FlowGuard\n\n## API\n\n`allow(key)` returns whether a request is permitted.\n",
            },
            symbols=["allow"],
            test_files=[],
        )
        rescore_checklist_with_evidence(checklist, evidence_untouched)
        self.assertEqual(checklist.items[0].status, "Not Addressed")

        checklist.items[0].status = "Not Addressed"
        evidence = bind_evidence(
            checklist,
            files_changed=["flowguard/rate_limiter.py", "README.md"],
            file_contents={
                "flowguard/rate_limiter.py": "def allow(key):\n    return True\n",
                "README.md": "# FlowGuard\n\n## API\n\n`allow(key)` returns whether a request is permitted.\n",
            },
            symbols=["allow"],
            test_files=[],
        )
        rescore_checklist_with_evidence(checklist, evidence)
        self.assertEqual(checklist.items[0].status, "Fully Addressed")

    def test_product_impl_not_only_test_file(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="FlowGuard",
            items=[
                RequirementItem(
                    text="Implement FlowGuard rate limiter allow and prune",
                    status="Not Addressed",
                    kind="product",
                )
            ],
        )
        evidence = bind_evidence(
            checklist,
            files_changed=["flowguard/rate_limiter.py", "tests/test_flowguard.py"],
            file_contents={
                "flowguard/rate_limiter.py": (
                    "class FlowGuard:\n"
                    "    def allow(self, key): return True\n"
                    "    def prune(self): pass\n"
                ),
                "tests/test_flowguard.py": "def test_allow(): assert True\n",
            },
            symbols=["FlowGuard", "allow", "prune"],
            test_files=["tests/test_flowguard.py"],
        )
        rescore_checklist_with_evidence(checklist, evidence)
        self.assertEqual(checklist.items[0].status, "Fully Addressed")

    def test_reconcile_helper(self):
        item = RequirementItem(text="Run the complete test suite", kind="verification")
        sig = DetectionSignals(tests_passed=True)
        self.assertEqual(
            reconcile_requirement_with_signals(item, sig), "Fully Addressed"
        )


class SuiteDiscoveryContradictionTest(unittest.TestCase):
    def test_no_relevant_tests_fp_when_suite_discovered_and_passed(self):
        sig = collect_base_signals(["flowguard/rate_limiter.py"])
        nodes = detect_missing_or_failing_tests(
            sig,
            relevant_tests=[],
            tests_passed=True,
            suite_discovered=7,
        )
        self.assertEqual(nodes, [])
        self.assertTrue(sig.tests_relevant_exist)

    def test_suite_failed_is_tests_failed_not_no_tests(self):
        sig = collect_base_signals(["flowguard/rate_limiter.py"])
        nodes = detect_missing_or_failing_tests(
            sig,
            relevant_tests=[],
            tests_passed=False,
            suite_discovered=7,
        )
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].signals.get("verify_state"), "TESTS_FAILED")
        self.assertNotIn("no relevant tests", nodes[0].summary.lower())


class CircuitBreakerTest(unittest.TestCase):
    def setUp(self):
        reset_outcomes()

    def tearDown(self):
        reset_outcomes()

    def test_zero_resolution_opens_circuit(self):
        for _ in range(12):
            record_outcome(NodeType.REQUIREMENT_GAP, "created")
        self.assertTrue(detector_circuit_open(NodeType.REQUIREMENT_GAP.value))
        bucket = {"created": 12, "resolved": 0, "ignored": 0, "force_override": 0, "medium_ack": 0}
        self.assertEqual(resolution_rate(bucket), 0.0)

    def test_noisy_detector_downgrades_gap_risk(self):
        for _ in range(12):
            record_outcome(NodeType.REQUIREMENT_GAP, "created")
        checklist = TaskChecklist(
            task_id="t1",
            title="x",
            items=[
                RequirementItem(
                    text="Add brand new widget API",
                    status="Not Addressed",
                    kind="product",
                )
            ],
        )
        sig = collect_base_signals(["app.py"])
        nodes = detect_requirement_gaps(sig, checklist=checklist)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].risk_tier, Tier.LOW)
        self.assertTrue(nodes[0].signals.get("detector_noisy"))


class BudgetAndLedgerTest(unittest.TestCase):
    def test_budget_caps_blocking_findings(self):
        nodes = [
            UncertaintyNode(
                title=f"b{i}",
                type=NodeType.MISSING_TEST,
                confidence_tier=Tier.LOW,
                risk_tier=Tier.HIGH,
                summary="s",
            )
            for i in range(6)
        ] + [
            UncertaintyNode(
                title="info",
                type=NodeType.TODO_COMMENT,
                confidence_tier=Tier.HIGH,
                risk_tier=Tier.LOW,
                summary="s",
            )
        ]
        capped = apply_uncertainty_budget(nodes, max_blocking=3)
        blocking = [n for n in capped if n.risk_tier == Tier.HIGH]
        self.assertLessEqual(len(blocking), 3)
        self.assertFalse(any(n.title == "info" for n in capped))

    def test_ledger_snapshot(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="x",
            items=[RequirementItem(text="Run the tests", kind="verification")],
        )
        record = VerificationRecord(
            ran=True,
            exit_code=0,
            tests_discovered=7,
            tests_passed=7,
            passed=True,
            state=VerifyState.TESTS_PASSED,
        )
        evidence = bind_evidence(
            checklist,
            files_changed=[],
            verification=record,
        )
        rescore_checklist_with_evidence(checklist, evidence)
        rows = ledger_snapshot(checklist, evidence)
        self.assertEqual(rows[0]["status"], "Fully Addressed")
        self.assertEqual(rows[0]["kind"], "verification")
        self.assertTrue(any("verify:ok" in e for e in rows[0]["evidence"]))


if __name__ == "__main__":
    unittest.main()
