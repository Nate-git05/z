"""Phases 6–7 — uncertainty subscribe/contract + skills near-dup authoring."""

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


if __name__ == "__main__":
    unittest.main()
