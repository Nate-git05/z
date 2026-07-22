"""Phases 6–10 — uncertainty, skills, commit gate, usage, MCP."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class UncertaintyPhase6Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="z_unc_p6_")
        self.env = mock.patch.dict(os.environ, {"Z_HOME": self.tmp})
        self.env.start()
        self.notes = []

        def notify(method, params):
            self.notes.append((method, params))

        from aider.z.app_server.handlers import AppServerSession

        self.session = AppServerSession(notify=notify)
        self.session.handle(
            "initialize",
            {"clientInfo": {"name": "t"}, "workspaceRoot": self.tmp},
        )

    def tearDown(self):
        self.session.dispose()
        self.env.stop()

    def _seed_node(self, **kwargs):
        from aider.z.uncertainty.schema import (
            Area,
            NodeStatus,
            NodeType,
            Tier,
            UncertaintyNode,
        )
        from aider.z.uncertainty.store import UncertaintyStore

        store = UncertaintyStore(root=self.tmp)
        node = UncertaintyNode(
            title=kwargs.get("title", "Untested path"),
            type=NodeType.MISSING_TEST,
            confidence_tier=Tier.LOW,
            risk_tier=kwargs.get("risk", Tier.HIGH),
            summary=kwargs.get("summary", "No tests for this change"),
            status=kwargs.get("status", NodeStatus.OPEN),
            area=Area.TESTS,
            task_title=kwargs.get("task_title", "Task A"),
        )
        store.add(node)
        return node

    def test_list_includes_resolution_contract(self):
        self._seed_node()
        result = self.session.handle("uncertainty/list", {"sort": "risk"})
        self.assertTrue(result["nodes"])
        node = result["nodes"][0]
        self.assertIn("resolution_contract", node)
        self.assertIsInstance(node["resolution_contract"], dict)
        self.assertIn("acceptable_evidence", node["resolution_contract"])

    def test_subscribe_emits_upsert_on_store_add(self):
        sub = self.session.handle("uncertainty/subscribe", {})
        self.assertTrue(sub["subscribed"])
        self.notes.clear()
        node = self._seed_node(title="Live node")
        upserts = [p for m, p in self.notes if m == "uncertainty/upsert"]
        self.assertTrue(upserts)
        self.assertEqual(upserts[-1]["node"]["id"], node.id)
        self.assertEqual(upserts[-1]["event"], "upsert")

    def test_sort_and_exclude_resolved(self):
        from aider.z.uncertainty.schema import NodeStatus, Tier

        self._seed_node(title="Open high", risk=Tier.HIGH)
        self._seed_node(title="Resolved", risk=Tier.LOW, status=NodeStatus.RESOLVED)
        open_only = self.session.handle("uncertainty/list", {"sort": "risk"})
        titles = [n["title"] for n in open_only["nodes"]]
        self.assertIn("Open high", titles)
        self.assertNotIn("Resolved", titles)
        all_nodes = self.session.handle(
            "uncertainty/list", {"includeResolved": True, "sort": "status"}
        )
        self.assertGreaterEqual(len(all_nodes["nodes"]), 2)


class SkillsPhase7Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="z_sk_p7_")
        self.env = mock.patch.dict(
            os.environ,
            {"Z_HOME": self.tmp, "Z_SKILL_NEAR_DUP": "1"},
        )
        self.env.start()
        from aider.z.app_server.handlers import AppServerSession

        self.session = AppServerSession()
        self.session.handle(
            "initialize",
            {"clientInfo": {"name": "t"}, "workspaceRoot": self.tmp},
        )

    def tearDown(self):
        self.session.dispose()
        self.env.stop()

    def test_create_draft_and_list_filters(self):
        created = self.session.handle(
            "skills/create",
            {
                "skill": {
                    "title": "Unique Playbook Alpha",
                    "description": "Do the thing carefully",
                    "content": "Step 1…",
                    "kind": "playbook",
                    "triggers": ["alpha-trigger"],
                    "capability": "alpha-cap",
                }
            },
        )
        self.assertTrue(created["created"])
        skill = created["skill"]
        self.assertEqual(skill["quality_state"], "draft")
        self.assertTrue(skill["needs_review"])
        self.assertEqual(skill["source"], "manual")

        listed = self.session.handle(
            "skills/list",
            {"kind": "playbook", "needs_review": True, "query": "alpha"},
        )
        self.assertEqual(len(listed["skills"]), 1)
        got = self.session.handle("skills/get", {"id": skill["id"]})
        self.assertEqual(got["skill"]["title"], "Unique Playbook Alpha")

    def test_near_dup_blocks_without_force(self):
        first = self.session.handle(
            "skills/create",
            {
                "skill": {
                    "title": "Race Condition Fix Pattern",
                    "description": "Detect and fix data races in concurrent code",
                    "content": "Use locks",
                    "kind": "bug_pattern",
                    "symptom_description": "flaky race in concurrent auth",
                    "root_cause_category": "concurrency",
                }
            },
        )
        self.assertTrue(first["created"])

        blocked = self.session.handle(
            "skills/create",
            {
                "skill": {
                    "title": "Race Condition Fix Pattern",
                    "description": "Detect and fix data races in concurrent code",
                    "content": "Use atomics",
                    "kind": "bug_pattern",
                    "symptom_description": "flaky race in concurrent auth",
                    "root_cause_category": "concurrency",
                }
            },
        )
        self.assertFalse(blocked["created"])
        self.assertIsNotNone(blocked.get("near_dup"))
        self.assertIn("Near-duplicate", blocked.get("message") or "")

        merged = self.session.handle(
            "skills/create",
            {
                "skill": {
                    "title": "Race Condition Fix Pattern",
                    "description": "Detect and fix data races in concurrent code",
                    "content": "Use atomics",
                    "kind": "bug_pattern",
                    "symptom_description": "flaky race in concurrent auth",
                    "root_cause_category": "concurrency",
                    "fix_technique": "prefer atomics",
                },
                "merge": True,
            },
        )
        self.assertTrue(merged["created"])
        self.assertTrue(merged["merged"])
        self.assertEqual(merged["skill"]["id"], first["skill"]["id"])


class CommitGatePhase8Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="z_gate_p8_")
        self.env = mock.patch.dict(os.environ, {"Z_HOME": self.tmp})
        self.env.start()
        self.notes = []
        from aider.z.app_server.handlers import AppServerSession

        self.session = AppServerSession(
            notify=lambda m, p: self.notes.append((m, p))
        )
        self.session.handle(
            "initialize",
            {"clientInfo": {"name": "t"}, "workspaceRoot": self.tmp},
        )

    def tearDown(self):
        self.session.dispose()
        self.env.stop()

    def test_override_requires_confirm(self):
        from aider.z.app_server.handlers import HandlerError
        from aider.z.uncertainty.commit_block_ledger import append_block

        rec = append_block(reason="tests failed", repo_key=self.tmp)
        with self.assertRaises(HandlerError) as ctx:
            self.session.handle(
                "commit_blocks/override",
                {"id": rec["id"]},
            )
        self.assertIn("confirm", str(ctx.exception).lower())

        out = self.session.handle(
            "commit_blocks/override",
            {"id": rec["id"], "confirm": True, "reason": "ship anyway"},
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["block"]["state"], "overridden")
        self.assertEqual(out["block"]["override_meta"]["reason"], "ship anyway")
        listed = self.session.handle("commit_blocks/list", {})
        self.assertTrue(listed["canCommit"])
        self.assertEqual(listed["blockedCount"], 0)
        updated = [p for m, p in self.notes if m == "gate/commit_updated"]
        self.assertTrue(updated)
        self.assertEqual(updated[-1]["action"], "overridden")

    def test_resolve_block(self):
        from aider.z.uncertainty.commit_block_ledger import append_block

        rec = append_block(reason="high risk", repo_key=self.tmp)
        out = self.session.handle(
            "commit_blocks/resolve",
            {"id": rec["id"], "note": "fixed tests"},
        )
        self.assertEqual(out["block"]["state"], "resolved")
        listed = self.session.handle("commit_blocks/list", {})
        self.assertTrue(listed["canCommit"])


class UsagePhase9Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="z_usage_p9_")
        self.env = mock.patch.dict(
            os.environ,
            {
                "Z_HOME": self.tmp,
                "Z_GATEWAY_USAGE_STUB": "",
                "Z_GATEWAY_STUB": "1",
            },
        )
        self.env.start()
        from aider.z.app_server.handlers import AppServerSession

        self.session = AppServerSession(notify=lambda *_: None)
        self.session.handle(
            "initialize",
            {"clientInfo": {"name": "t"}, "workspaceRoot": self.tmp},
        )

    def tearDown(self):
        self.session.dispose()
        self.env.stop()

    def test_usage_summary_stub_shape(self):
        out = self.session.handle("usage/summary", {"range": "billing_period"})
        self.assertEqual(out["range"], "billing_period")
        self.assertIn("byModel", out)
        self.assertGreaterEqual(out["totalRequests"], 0)
        self.assertIn("totalCostUsd", out)
        self.assertTrue(out["byModel"])
        row = out["byModel"][0]
        self.assertIn("model_id", row)
        self.assertIn("requests", row)
        self.assertIn("cost_usd", row)

    def test_usage_summary_all_range(self):
        out = self.session.handle("usage/summary", {"range": "all"})
        self.assertEqual(out["range"], "all")

    def test_normalize_gateway_payload(self):
        from aider.z.usage_client import normalize_for_profile

        norm = normalize_for_profile(
            {
                "range": "billing_period",
                "by_model": [
                    {
                        "model_id": "z-composer",
                        "requests": 3,
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cost_usd": 0.12,
                    }
                ],
                "total_requests": 3,
                "total_cost_usd": 0.12,
                "source": "gateway",
                "authenticated": True,
            }
        )
        self.assertEqual(norm["totalRequests"], 3)
        self.assertEqual(norm["totalCostUsd"], 0.12)
        self.assertEqual(norm["byModel"][0]["modelId"], "z-composer")


class McpPhase10Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="z_mcp_p10_")
        self.mcp_dir = str(Path(self.tmp) / "mcp")
        self.env = mock.patch.dict(
            os.environ,
            {"Z_HOME": self.tmp, "Z_MCP_DIR": self.mcp_dir},
        )
        self.env.start()
        from aider.z.app_server.handlers import AppServerSession

        self.session = AppServerSession(notify=lambda *_: None)
        self.session.handle(
            "initialize",
            {"clientInfo": {"name": "t"}, "workspaceRoot": self.tmp},
        )

    def tearDown(self):
        self.session.dispose()
        self.env.stop()

    def test_catalog_and_connect_list_disconnect(self):
        cat = self.session.handle("mcp/catalog", {})
        self.assertTrue(cat["catalog"])
        names = {e["serverName"] for e in cat["catalog"]}
        self.assertIn("github", names)
        self.assertIn("custom", names)

        connected = self.session.handle(
            "mcp/connect",
            {
                "serverName": "github",
                "credentials": {"token": "ghp_test_token"},
                "syncCloud": False,
            },
        )
        self.assertIn("connection", connected)
        cid = connected["connection"]["id"]
        self.assertEqual(connected["connection"]["serverName"], "github")
        self.assertTrue(connected["connection"]["hasSecrets"])

        listed = self.session.handle("mcp/list", {})
        self.assertTrue(
            any(
                (c.get("serverName") or c.get("server_name")) == "github"
                for c in listed["connections"]
            )
        )

        tested = self.session.handle("mcp/test", {"id": cid})
        self.assertTrue(tested["ok"])

        gone = self.session.handle("mcp/disconnect", {"id": cid})
        self.assertTrue(gone["ok"])
        listed2 = self.session.handle("mcp/list", {})
        self.assertFalse(
            any(
                (c.get("serverName") or c.get("server_name")) == "github"
                for c in listed2["connections"]
            )
        )

    def test_test_requires_fields(self):
        out = self.session.handle(
            "mcp/test",
            {"serverName": "github", "credentials": {}, "skipPersist": True},
        )
        self.assertFalse(out["ok"])
        self.assertIn("token", str(out.get("error", "")).lower())

    def test_first_use_confirm_gate(self):
        from aider.z import mcp_local

        self.assertTrue(mcp_local.needs_first_use_confirm("github", "list_issues"))
        st = self.session.handle(
            "mcp/firstUseStatus",
            {"serverName": "github", "toolName": "list_issues"},
        )
        self.assertTrue(st["needsConfirm"])
        conf = self.session.handle(
            "mcp/confirmFirstUse",
            {"serverName": "github", "toolName": "list_issues", "forever": True},
        )
        self.assertTrue(conf["ok"])
        st2 = self.session.handle(
            "mcp/firstUseStatus",
            {"serverName": "github", "toolName": "list_issues"},
        )
        self.assertFalse(st2["needsConfirm"])
        self.assertFalse(mcp_local.needs_first_use_confirm("github", "list_issues"))

    def test_capabilities_include_usage_and_mcp_manage(self):
        # re-initialize already done; check capability list via fresh init result
        from aider.z.app_server.handlers import AppServerSession

        s = AppServerSession(notify=lambda *_: None)
        info = s.handle("initialize", {"clientInfo": {"name": "t2"}})
        caps = info["capabilities"]
        self.assertIn("usage", caps)
        self.assertIn("mcp_manage", caps)
        s.dispose()


if __name__ == "__main__":
    unittest.main()
