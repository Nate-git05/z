"""Tests for human-like uncertainty: context, checklist evidence, auto-act, detectors."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_Z_HOME = tempfile.mkdtemp(prefix="z_human_unc_")
os.environ["Z_HOME"] = _Z_HOME

from aider.z.uncertainty.auto_act import (  # noqa: E402
    default_prompt_for_node,
    plan_auto_act,
    select_auto_act_targets,
)
from aider.z.uncertainty.checklist import (  # noqa: E402
    bind_evidence,
    checklist_gap_details,
    decompose_request,
    rescore_checklist_with_evidence,
)
from aider.z.uncertainty.context import (  # noqa: E402
    assess_repo_maturity,
    is_scaffold_file,
    should_emit_new_file_noise,
)
from aider.z.uncertainty.detectors import (  # noqa: E402
    PatternSearchResult,
    detect_failure_blind_spots,
    detect_fragile_logic,
    detect_high_stakes_and_migration,
    detect_pattern_issues,
    detect_requirement_gaps,
)
from aider.z.uncertainty.risk import collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeStatus,
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
    UncertaintyNode,
    parse_node_type,
)
from aider.z.uncertainty.store import UncertaintyStore  # noqa: E402


class SchemaHumanNamesTest(unittest.TestCase):
    def test_display_values_are_human(self):
        self.assertEqual(NodeType.MISSING_TEST.value, "Untested Path")
        self.assertEqual(NodeType.API_ASSUMPTION.value, "Unverified Assumption")
        self.assertEqual(NodeType.HIGH_CONFIDENCE.value, "Evidence of Safety")
        self.assertEqual(NodeType.HIGH_STAKES.value, "High-Stakes Surface")

    def test_legacy_aliases_parse(self):
        self.assertEqual(parse_node_type("Missing Test"), NodeType.MISSING_TEST)
        self.assertEqual(parse_node_type("API Assumption"), NodeType.API_ASSUMPTION)
        self.assertEqual(parse_node_type("Untested Path"), NodeType.MISSING_TEST)


class ContextNoiseTest(unittest.TestCase):
    def test_scaffold_detection(self):
        self.assertTrue(is_scaffold_file("README.md"))
        self.assertTrue(is_scaffold_file("pkg/__init__.py"))
        self.assertFalse(is_scaffold_file("pkg/billing.py"))

    def test_greenfield_suppresses_new_file_noise(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# hi\n", encoding="utf-8")
            (root / "app.py").write_text("x=1\n", encoding="utf-8")
            maturity = assess_repo_maturity(root)
            self.assertEqual(maturity, "greenfield")
            self.assertFalse(should_emit_new_file_noise(maturity))

    def test_pattern_issues_respect_emit_flags(self):
        sig = collect_base_signals(["lib/brand_new_thing.py"])
        nodes = detect_pattern_issues(
            sig,
            new_files=["lib/brand_new_thing.py"],
            pattern_results={"lib/brand_new_thing.py": PatternSearchResult(matches=[])},
            emit_new_file_noise=False,
        )
        self.assertEqual(nodes, [])


class ChecklistEvidenceTest(unittest.TestCase):
    def test_bind_and_rescore_not_addressed(self):
        checklist = decompose_request(
            "Billing",
            "Add Stripe checkout and send receipt emails",
        )
        # Only checkout evidence in files — receipt should stay Not/Partial
        evidence = bind_evidence(
            checklist,
            files_changed=["payments/checkout.py"],
            file_contents={
                "payments/checkout.py": "def create_checkout():\n    stripe.Charge.create()\n"
            },
            symbols=["create_checkout"],
            test_files=[],
        )
        rescore_checklist_with_evidence(checklist, evidence)
        statuses = {i.text.lower(): i.status for i in checklist.items}
        # At least one item should not be Fully if receipt has no evidence
        receipt_items = [i for i in checklist.items if "receipt" in i.text.lower()]
        if receipt_items:
            self.assertNotEqual(receipt_items[0].status, "Fully Addressed")
        gaps = checklist_gap_details(checklist, evidence)
        self.assertTrue(gaps)

    def test_test_only_evidence_is_partial(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="Feat",
            items=[RequirementItem(text="Implement webhook signature verification")],
        )
        evidence = bind_evidence(
            checklist,
            files_changed=[],
            file_contents={},
            symbols=[],
            test_files=["tests/test_webhook_signature.py"],
        )
        rescore_checklist_with_evidence(checklist, evidence)
        self.assertEqual(checklist.items[0].status, "Partially Addressed")


class HumanDetectorsTest(unittest.TestCase):
    def test_high_stakes_node_type(self):
        sig = collect_base_signals(["billing/webhook.py"])
        nodes = detect_high_stakes_and_migration(
            sig, file_contents={"billing/webhook.py": "def handle_stripe():\n    pass\n"}
        )
        self.assertTrue(any(n.type == NodeType.HIGH_STAKES for n in nodes))

    def test_fragile_logic(self):
        sig = collect_base_signals(["svc.py"])
        text = (
            "def f(x):\n"
            "    try:\n"
            "        if x:\n"
            "            if x > 1:\n"
            "                if x > 2:\n"
            "                    if x > 3:\n"
            "                        return 1\n"
            "    except:\n"
            "        pass\n"
        )
        nodes = detect_fragile_logic(sig, file_contents={"svc.py": text})
        self.assertTrue(nodes)
        self.assertEqual(nodes[0].type, NodeType.FRAGILE_LOGIC)

    def test_failure_blind_spot(self):
        sig = collect_base_signals(["client.py"])
        text = "import requests\n\ndef go():\n    return requests.get('https://example.com')\n"
        nodes = detect_failure_blind_spots(sig, file_contents={"client.py": text})
        self.assertTrue(nodes)
        self.assertEqual(nodes[0].type, NodeType.FAILURE_BLIND_SPOT)

    def test_requirement_gap_includes_missing(self):
        sig = collect_base_signals(["app.py"])
        checklist = TaskChecklist(
            task_id="t1",
            title="Billing",
            items=[
                RequirementItem(text="Send receipt email", status="Not Addressed"),
            ],
        )
        nodes = detect_requirement_gaps(
            sig,
            checklist=checklist,
            gap_details=[
                {
                    "id": checklist.items[0].id,
                    "missing": "No receipt email sender implemented",
                    "evidence": [],
                    "status": "Not Addressed",
                }
            ],
        )
        self.assertEqual(nodes[0].signals.get("missing"), "No receipt email sender implemented")


class AutoActTest(unittest.TestCase):
    def test_selects_high_untested_and_gaps(self):
        nodes = [
            UncertaintyNode(
                title="Untested",
                type=NodeType.MISSING_TEST,
                confidence_tier=Tier.LOW,
                risk_tier=Tier.HIGH,
                summary="no tests",
            ),
            UncertaintyNode(
                title="Gap",
                type=NodeType.REQUIREMENT_GAP,
                confidence_tier=Tier.LOW,
                risk_tier=Tier.MEDIUM,
                summary="gap",
                signals={"requirement_status": "Not Addressed"},
            ),
            UncertaintyNode(
                title="Low todo",
                type=NodeType.TODO_COMMENT,
                confidence_tier=Tier.MEDIUM,
                risk_tier=Tier.LOW,
                summary="todo",
            ),
        ]
        targets = select_auto_act_targets(nodes)
        self.assertTrue(any(t.type == NodeType.MISSING_TEST for t in targets))
        self.assertTrue(default_prompt_for_node(targets[0]))

    def test_plan_auto_act_reflects_once(self):
        store = UncertaintyStore(repo_key="auto-act-test")
        node = UncertaintyNode(
            title="Gap",
            type=NodeType.REQUIREMENT_GAP,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.HIGH,
            summary="missing feature",
            signals={"requirement_status": "Not Addressed", "missing": "Build receipts"},
        )
        store.add(node, sync=False)
        result = plan_auto_act(store, [node], attempts=0, max_attempts=1)
        self.assertIsNotNone(result.reflect_message)
        self.assertIn("Requirement gap", result.reflect_message)
        self.assertEqual(store.get(node.id).status, NodeStatus.IN_PROGRESS)
        # Second attempt exhausted
        result2 = plan_auto_act(store, [node], attempts=1, max_attempts=1)
        self.assertIsNone(result2.reflect_message)


if __name__ == "__main__":
    unittest.main()
