"""Tests for Z uncertainty tree — detectors, tiers, store, and API."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# Isolate local store
_Z_HOME = tempfile.mkdtemp(prefix="z_unc_home_")
os.environ["Z_HOME"] = _Z_HOME

from aider.z.uncertainty.actions import apply_action  # noqa: E402
from aider.z.uncertainty.checklist import (  # noqa: E402
    decompose_request,
    infer_gap_statuses_from_summary,
)
from aider.z.uncertainty.detectors import (  # noqa: E402
    PatternSearchResult,
    detect_api_assumptions,
    detect_blast_radius,
    detect_edge_cases,
    detect_high_confidence,
    detect_high_stakes_and_migration,
    detect_missing_or_failing_tests,
    detect_pattern_issues,
    detect_requirement_gaps,
    detect_todo_comments,
    detect_unverifiable_config,
    find_relevant_tests,
)
from aider.z.uncertainty.engine import SessionContext, UncertaintyEngine  # noqa: E402
from aider.z.uncertainty.risk import (  # noqa: E402
    DetectionSignals,
    collect_base_signals,
    derive_confidence_tier,
    derive_risk_tier,
)
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeStatus,
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
    UncertaintyNode,
)
from aider.z.uncertainty.store import UncertaintyStore, sort_nodes  # noqa: E402
from aider.z.uncertainty.tree import build_tree, flatten_for_display  # noqa: E402
from aider.z.uncertainty.ui import format_collapsed, format_detail  # noqa: E402


class TierDerivationTest(unittest.TestCase):
    def test_risk_and_confidence_independent(self):
        # Low confidence + high risk (failing tests on high-stakes)
        sig = DetectionSignals(
            files_changed=["billing/webhook.py"],
            high_stakes_hit=True,
            tests_relevant_exist=True,
            tests_passed=False,
        )
        risk = derive_risk_tier(sig, NodeType.MISSING_TEST)
        conf = derive_confidence_tier(sig, NodeType.MISSING_TEST)
        self.assertEqual(risk, Tier.HIGH)
        self.assertEqual(conf, Tier.LOW)

    def test_high_confidence_still_medium_risk_when_high_stakes(self):
        sig = DetectionSignals(
            files_changed=["auth/login.py"],
            high_stakes_hit=True,
            closely_matches_tested_pattern=True,
            tests_relevant_exist=True,
            tests_passed=True,
        )
        risk = derive_risk_tier(sig, NodeType.HIGH_CONFIDENCE)
        conf = derive_confidence_tier(sig, NodeType.HIGH_CONFIDENCE)
        self.assertIn(risk, (Tier.MEDIUM, Tier.HIGH))
        self.assertEqual(conf, Tier.HIGH)

    def test_payment_path_forces_medium_risk(self):
        sig = collect_base_signals(["src/payment/checkout.py"])
        self.assertTrue(sig.high_stakes_hit)
        risk = derive_risk_tier(sig, NodeType.EDGE_CASE)
        self.assertNotEqual(risk, Tier.LOW)


class DetectorTest(unittest.TestCase):
    def test_missing_test(self):
        sig = collect_base_signals(["app/foo.py"], ["foo"])
        nodes = detect_missing_or_failing_tests(sig, relevant_tests=[], tests_passed=None)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].type, NodeType.MISSING_TEST)

    def test_failing_test_escalates(self):
        sig = collect_base_signals(["app/foo.py"])
        nodes = detect_missing_or_failing_tests(
            sig, relevant_tests=["tests/test_foo.py"], tests_passed=False
        )
        self.assertEqual(nodes[0].status, NodeStatus.NEEDS_HUMAN_REVIEW)
        self.assertEqual(nodes[0].risk_tier, Tier.HIGH)

    def test_migration_risk(self):
        sig = collect_base_signals(["alembic/versions/001_add_users.py"])
        nodes = detect_high_stakes_and_migration(
            sig, migration_data_impact="Existing rows get null email until backfill."
        )
        self.assertTrue(any(n.type == NodeType.MIGRATION_RISK for n in nodes))
        self.assertNotEqual(nodes[0].risk_tier, Tier.LOW)

    def test_api_assumption_without_live_call(self):
        sig = collect_base_signals(["integrations/stripe_client.py"])
        nodes = detect_api_assumptions(
            sig,
            assumed_apis=["stripe"],
            live_verified_apis=set(),
        )
        self.assertEqual(nodes[0].type, NodeType.API_ASSUMPTION)
        self.assertEqual(nodes[0].confidence_tier, Tier.LOW)

    def test_mcp_unverifiable(self):
        sig = collect_base_signals(["app.py"])
        nodes = detect_api_assumptions(
            sig,
            assumed_apis=[],
            live_verified_apis=set(),
            mcp_unverifiable=["github"],
        )
        self.assertEqual(nodes[0].type, NodeType.API_ASSUMPTION)
        self.assertIn("github", nodes[0].title.lower())

    def test_new_file_no_pattern(self):
        sig = collect_base_signals(["lib/brand_new_thing.py"])
        nodes = detect_pattern_issues(
            sig,
            new_files=["lib/brand_new_thing.py"],
            pattern_results={"lib/brand_new_thing.py": PatternSearchResult(matches=[])},
        )
        self.assertEqual(nodes[0].type, NodeType.NEW_FILE_NO_PATTERN)

    def test_pattern_inconsistency(self):
        sig = collect_base_signals(["lib/widget.py"])
        nodes = detect_pattern_issues(
            sig,
            new_files=["lib/widget.py"],
            pattern_results={
                "lib/widget.py": PatternSearchResult(
                    matches=["a/widget.py", "b/widget.py", "c/widget.py"],
                    conflicting=True,
                )
            },
        )
        self.assertEqual(nodes[0].type, NodeType.PATTERN_INCONSISTENCY)

    def test_blast_radius(self):
        sig = collect_base_signals(["core/utils.py"], ["shared_helper"])
        sig.blast_radius_threshold = 5
        nodes = detect_blast_radius(
            sig, reference_count=12, referenced_symbol="shared_helper"
        )
        self.assertEqual(nodes[0].type, NodeType.SHARED_LOGIC)
        self.assertIn("12", nodes[0].explanation)
        self.assertIn("5", nodes[0].explanation)

    def test_todo_comment(self):
        sig = collect_base_signals(["app.py"])
        nodes = detect_todo_comments(
            sig, todos_by_file={"app.py": ["L10: # TODO: handle retries"]}
        )
        self.assertEqual(nodes[0].type, NodeType.TODO_COMMENT)

    def test_unverifiable_config(self):
        sig = collect_base_signals(["settings.py"])
        nodes = detect_unverifiable_config(
            sig,
            config_refs_by_file={"settings.py": ["STRIPE_SECRET_KEY", "os.environ"]},
            accessible_env_keys=set(),
        )
        self.assertEqual(nodes[0].type, NodeType.UNVERIFIABLE_CONFIG)

    def test_edge_cases_each_own_node(self):
        sig = collect_base_signals(["cart.py"])
        nodes = detect_edge_cases(
            sig, edge_cases=["Double-click checkout", "Empty cart coupon"]
        )
        self.assertEqual(len(nodes), 2)
        self.assertTrue(all(n.type == NodeType.EDGE_CASE for n in nodes))

    def test_requirement_gap(self):
        sig = collect_base_signals(["app.py"])
        checklist = TaskChecklist(
            task_id="t1",
            title="Billing",
            items=[
                RequirementItem(text="Add Stripe checkout", status="Fully Addressed"),
                RequirementItem(text="Send receipt email", status="Not Addressed"),
            ],
        )
        nodes = detect_requirement_gaps(sig, checklist=checklist)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].type, NodeType.REQUIREMENT_GAP)

    def test_high_confidence_positive(self):
        sig = DetectionSignals(
            files_changed=["handlers/list.py"],
            closely_matches_tested_pattern=True,
            tests_relevant_exist=True,
            tests_passed=True,
        )
        nodes = detect_high_confidence(sig)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].type, NodeType.HIGH_CONFIDENCE)
        self.assertEqual(nodes[0].confidence_tier, Tier.HIGH)


class StoreAndTreeTest(unittest.TestCase):
    def test_sort_risk_first(self):
        nodes = [
            UncertaintyNode(
                title="a",
                type=NodeType.EDGE_CASE,
                confidence_tier=Tier.HIGH,
                risk_tier=Tier.LOW,
                summary="x",
            ),
            UncertaintyNode(
                title="b",
                type=NodeType.EDGE_CASE,
                confidence_tier=Tier.HIGH,
                risk_tier=Tier.HIGH,
                summary="x",
            ),
            UncertaintyNode(
                title="c",
                type=NodeType.EDGE_CASE,
                confidence_tier=Tier.LOW,
                risk_tier=Tier.MEDIUM,
                summary="x",
            ),
        ]
        ordered = sort_nodes(nodes)
        self.assertEqual([n.title for n in ordered], ["b", "c", "a"])

    def test_store_persist_and_action(self):
        store = UncertaintyStore(repo_key="test-repo-actions")
        node = UncertaintyNode(
            title="Possible duplicate checkout",
            type=NodeType.EDGE_CASE,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.HIGH,
            summary="Clicks may double-charge.",
            suggested_prompt="Fix duplicate checkout clicks.",
        )
        store.add(node, sync=False)
        result = apply_action(store, node, "ignore")
        self.assertEqual(result.status, NodeStatus.IGNORED)
        self.assertEqual(store.get(node.id).status, NodeStatus.IGNORED)

        fix_node = UncertaintyNode(
            title="Need test",
            type=NodeType.MISSING_TEST,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.MEDIUM,
            summary="No tests",
            files_affected=["a.py"],
        )
        store.add(fix_node, sync=False)
        result = apply_action(store, fix_node, "fix")
        self.assertEqual(result.status, NodeStatus.IN_PROGRESS)
        self.assertTrue(result.prompt)

    def test_tree_groups_by_area(self):
        nodes = [
            UncertaintyNode(
                title="fe",
                type=NodeType.EDGE_CASE,
                confidence_tier=Tier.MEDIUM,
                risk_tier=Tier.LOW,
                summary="s",
                files_affected=["src/App.tsx"],
                task_title="Add Stripe Billing",
            ),
            UncertaintyNode(
                title="db",
                type=NodeType.MIGRATION_RISK,
                confidence_tier=Tier.MEDIUM,
                risk_tier=Tier.HIGH,
                summary="s",
                files_affected=["migrations/001.py"],
                task_title="Add Stripe Billing",
            ),
        ]
        # Fix areas via schema infer — set explicitly
        from aider.z.uncertainty.schema import Area

        nodes[0].area = Area.FRONTEND
        nodes[1].area = Area.DATABASE
        tree = build_tree(nodes, mode="risk")
        self.assertIn("Add Stripe Billing", tree.children)
        task = tree.children["Add Stripe Billing"]
        self.assertIn("Frontend", task.children)
        self.assertIn("Database", task.children)
        flat = flatten_for_display(tree, mode="risk")
        self.assertEqual(flat[0][1].title, "db")  # high risk first

    def test_collapsed_ui_no_emoji_no_percent(self):
        node = UncertaintyNode(
            title="Possible duplicate checkout clicks",
            type=NodeType.EDGE_CASE,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.HIGH,
            summary="s",
        )
        text = format_collapsed(node, color=False)
        self.assertIn("Possible duplicate checkout clicks", text)
        self.assertIn("Edge Case", text)
        self.assertIn("risk=High", text)
        self.assertNotIn("%", text)
        self.assertNotIn("⚠", text)
        detail = format_detail(node, color=False)
        self.assertIn("Confidence: Low", detail)
        self.assertIn("Risk: High", detail)


class ChecklistTest(unittest.TestCase):
    def test_decompose_bullets(self):
        msg = "Please:\n- Add Stripe checkout\n- Email receipts\n- Update docs"
        cl = decompose_request("Billing", msg)
        self.assertGreaterEqual(len(cl.items), 3)
        statuses = infer_gap_statuses_from_summary(
            cl, "Added stripe checkout and email receipts in billing.py"
        )
        addressed = [i.status for i in statuses.items]
        # Lexical summary must never grant Fully — fail closed (Partial at most)
        self.assertNotIn("Fully Addressed", addressed)
        self.assertIn("Partially Addressed", addressed)
        self.assertTrue(any(s == "Not Addressed" for s in addressed))


class EngineIntegrationTest(unittest.TestCase):
    def test_analyze_edits_generates_nodes(self):
        root = Path(tempfile.mkdtemp(prefix="z_unc_repo_"))
        payment = root / "billing" / "checkout.py"
        payment.parent.mkdir(parents=True)
        payment.write_text(
            "# TODO: idempotency\nimport stripe\nSTRIPE_SECRET_KEY = os.environ['STRIPE_SECRET_KEY']\n",
            encoding="utf-8",
        )
        store = UncertaintyStore(root=root, repo_key=str(root) + "-eng")
        ctx = SessionContext(root=root, store=store, session_id="sess-1")
        engine = UncertaintyEngine(ctx)
        engine.begin_task("Add Stripe checkout and receipt emails")
        engine.record_assumed_api("stripe")
        engine.record_edge_cases(["Double-click submit"])
        nodes = engine.analyze_edits(
            ["billing/checkout.py"],
            symbols=["checkout"],
            tests_passed=None,
        )
        types = {n.type for n in nodes}
        self.assertIn(NodeType.MISSING_TEST, types)
        self.assertTrue(
            NodeType.TODO_COMMENT in types
            or NodeType.API_ASSUMPTION in types
            or NodeType.EDGE_CASE in types
            or NodeType.UNVERIFIABLE_CONFIG in types
        )
        # Risk-first listing
        listed = store.list()
        self.assertTrue(listed)
        self.assertEqual(listed[0].risk_tier, sort_nodes(listed)[0].risk_tier)

    def test_find_relevant_tests(self):
        root = Path(tempfile.mkdtemp(prefix="z_unc_tests_"))
        (root / "mod.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        (root / "test_mod.py").write_text("from mod import foo\n\ndef test_foo():\n    assert foo()==1\n", encoding="utf-8")
        found = find_relevant_tests(root, ["mod.py"], ["foo"])
        self.assertTrue(any("test_mod" in f for f in found))


# --- API persistence ---

_DB_PATH = tempfile.mktemp(suffix="_z_unc_api.db")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
os.environ["Z_SECRET_KEY"] = "test-secret-unc"
os.environ["Z_SERVER_DEV"] = "1"
os.environ["Z_PUBLIC_BASE_URL"] = "http://testserver"

from z_server.config import get_settings  # noqa: E402

get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from z_server.app import create_app  # noqa: E402
from z_server.db import init_db, reset_engine  # noqa: E402


class UncertaintyApiTest(unittest.TestCase):
    def setUp(self):
        reset_engine()
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
        if os.path.exists(_DB_PATH):
            os.unlink(_DB_PATH)
        init_db()
        self.app = create_app()
        self.client = TestClient(self.app)
        resp = self.client.post(
            "/v1/auth/email/start",
            json={"email": "unc@example.com", "name": "Unc"},
        )
        self.assertEqual(resp.status_code, 200)
        verify = self.client.post(
            "/v1/auth/email/verify",
            json={"email": "unc@example.com", "code": "000000", "name": "Unc"},
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        self.headers = {"Authorization": f"Bearer {verify.json()['access_token']}"}

    def tearDown(self):
        reset_engine()
        get_settings.cache_clear()
        if os.path.exists(_DB_PATH):
            try:
                os.unlink(_DB_PATH)
            except OSError:
                pass

    def test_upsert_list_patch(self):
        node = {
            "id": "11111111-1111-1111-1111-111111111111",
            "title": "Possible duplicate checkout clicks",
            "type": "Edge Case",
            "confidence_tier": "Low",
            "risk_tier": "High",
            "summary": "Double submit may charge twice.",
            "explanation": "No idempotency key.",
            "files_affected": ["billing/checkout.py"],
            "symbols_affected": ["checkout"],
            "why_uncertain": "No lock on submit.",
            "what_could_go_wrong": "Double charge.",
            "suggested_fix": "Add idempotency key.",
            "suggested_tests": ["test double click"],
            "suggested_prompt": "Fix duplicate checkout clicks.",
            "status": "Open",
            "area": "Backend",
            "created_by_session": "sess-abc",
            "signals": {"reference_count": 0},
        }
        resp = self.client.post(
            "/v1/uncertainty/nodes",
            headers=self.headers,
            json={"repo_key": "/repo/demo", "node": node},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        listed = self.client.get(
            "/v1/uncertainty/nodes",
            headers=self.headers,
            params={"repo_key": "/repo/demo"},
        )
        self.assertEqual(listed.status_code, 200)
        nodes = listed.json()["nodes"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["risk_tier"], "High")
        self.assertEqual(nodes[0]["created_by_session"], "sess-abc")

        patched = self.client.patch(
            f"/v1/uncertainty/nodes/{node['id']}",
            headers=self.headers,
            json={"status": "Ignored"},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["node"]["status"], "Ignored")


if __name__ == "__main__":
    unittest.main()
