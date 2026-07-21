"""Tests for z-app-server handlers + commit-block ledger (Phase 0)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CommitBlockLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="z_cbl_")
        self.env = mock.patch.dict(os.environ, {"Z_HOME": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_append_and_list(self):
        from aider.z.uncertainty.commit_block_ledger import append_block, list_blocks

        rec = append_block(
            reason="tests failed",
            repo_key="/tmp/demo-repo",
            session_id="sess-1",
            verify_state="TESTS_FAILED",
        )
        self.assertEqual(rec["state"], "blocked")
        self.assertTrue(rec["id"])
        blocks = list_blocks(repo_key="/tmp/demo-repo")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["reason"], "tests failed")
        self.assertEqual(blocks[0]["session_id"], "sess-1")

    def test_set_block_state(self):
        from aider.z.uncertainty.commit_block_ledger import (
            append_block,
            list_blocks,
            set_block_state,
        )

        rec = append_block(reason="high risk", repo_key="r1")
        updated = set_block_state(
            rec["id"],
            "overridden",
            repo_key="r1",
            override_meta={"by": "test"},
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["state"], "overridden")
        self.assertEqual(list_blocks(repo_key="r1")[0]["state"], "overridden")


class AppServerHandlersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="z_appsrv_")
        self.skills = Path(self.tmp) / "skills"
        self.skills.mkdir()
        self.env = mock.patch.dict(
            os.environ,
            {"Z_HOME": self.tmp},
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_initialize_and_not_before(self):
        from aider.z.app_server.handlers import AppServerSession, HandlerError

        s = AppServerSession()
        with self.assertRaises(HandlerError):
            s.handle("uncertainty/list", {})
        result = s.handle(
            "initialize",
            {"clientInfo": {"name": "test", "version": "0"}, "workspaceRoot": self.tmp},
        )
        self.assertEqual(result["serverInfo"]["name"], "z-app-server")
        self.assertIn("uncertainty", result["capabilities"])
        self.assertEqual(result["workspaceRoot"], self.tmp)

    def test_turn_start_requires_workspace_and_text(self):
        from aider.z.app_server.handlers import AppServerSession, HandlerError

        s = AppServerSession()
        s.handle("initialize", {"clientInfo": {"name": "t"}})
        with self.assertRaises(HandlerError):
            s.handle("turn/start", {})
        with self.assertRaises(HandlerError) as ctx:
            s.handle("turn/start", {"text": "hello", "threadId": "t1"})
        self.assertIn("workspace", str(ctx.exception).lower())

    def test_turn_start_accepts_with_workspace(self):
        from aider.z.app_server.handlers import AppServerSession

        s = AppServerSession()
        s.handle(
            "initialize",
            {"clientInfo": {"name": "t"}, "workspaceRoot": self.tmp},
        )
        with mock.patch.object(s, "_turn_manager") as tm:
            mgr = mock.Mock()
            mgr.start.return_value = {
                "turnId": "tid-1",
                "threadId": "t1",
                "accepted": True,
                "queued": False,
                "stub": False,
            }
            tm.return_value = mgr
            out = s.handle("turn/start", {"text": "hello", "threadId": "t1"})
        self.assertTrue(out["accepted"])
        self.assertFalse(out.get("stub"))
        mgr.start.assert_called_once_with(text="hello", thread_id="t1")

    def test_skills_create_is_draft(self):
        from aider.z.app_server.handlers import AppServerSession
        from aider.z.skills.store import LocalSkillStore, skills_dir

        s = AppServerSession()
        s.handle("initialize", {"clientInfo": {"name": "t"}})
        out = s.handle(
            "skills/create",
            {
                "skill": {
                    "title": "Manual demo skill",
                    "description": "authored in app",
                    "content": "## Steps\n1. Do the thing\n",
                    "kind": "playbook",
                }
            },
        )
        skill = out["skill"]
        self.assertEqual(skill["source"], "manual")
        self.assertEqual(skill["quality_state"], "draft")
        self.assertTrue(skill["needs_review"])
        store = LocalSkillStore(root=skills_dir())
        listed = store.list_skills()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].title, "Manual demo skill")

    def test_commit_blocks_list(self):
        from aider.z.app_server.handlers import AppServerSession
        from aider.z.uncertainty.commit_block_ledger import append_block

        append_block(reason="blocked", repo_key=self.tmp)
        s = AppServerSession()
        s.handle(
            "initialize",
            {"clientInfo": {"name": "t"}, "workspaceRoot": self.tmp},
        )
        out = s.handle("commit_blocks/list", {})
        self.assertEqual(len(out["blocks"]), 1)
        self.assertEqual(out["blocks"][0]["reason"], "blocked")

    def test_protocol_helpers(self):
        from aider.z.app_server.protocol import make_error, make_result, parse_message

        self.assertEqual(make_result(1, {"ok": True})["result"]["ok"], True)
        err = make_error(2, -32601, "nope")
        self.assertEqual(err["error"]["code"], -32601)
        self.assertEqual(parse_message('{"id":1,"method":"initialize"}')["method"], "initialize")


class GatewayClientTest(unittest.TestCase):
    def test_openai_compatible_model(self):
        from aider.z.gateway_client import openai_compatible_model

        self.assertEqual(openai_compatible_model("gpt-4o"), "openai/gpt-4o")
        self.assertEqual(
            openai_compatible_model("anthropic/claude-3-5-sonnet"),
            "openai/claude-3-5-sonnet",
        )
        self.assertEqual(openai_compatible_model("openai/gpt-4o"), "openai/gpt-4o")


class LifecycleHelpersTest(unittest.TestCase):
    def test_parse_host_port(self):
        from aider.z.app_server.lifecycle import parse_host_port, resolve_app_server_url

        self.assertEqual(parse_host_port("ws://127.0.0.1:8741"), ("127.0.0.1", 8741))
        self.assertEqual(parse_host_port("127.0.0.1:9000"), ("127.0.0.1", 9000))
        self.assertTrue(resolve_app_server_url().startswith("ws://"))

    def test_port_is_open_false(self):
        from aider.z.app_server.lifecycle import port_is_open

        # Unlikely anything listens on this high port in the test sandbox
        self.assertFalse(port_is_open("127.0.0.1", 59999, timeout=0.05))


class Phase3AuthWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="z_p3_")
        self.env = mock.patch.dict(os.environ, {"Z_HOME": self.tmp})
        self.env.start()
        # Fresh login controller per test
        from aider.z.app_server import login_session

        login_session.controller = login_session.EditorLoginController()

    def tearDown(self):
        from aider.z.app_server import login_session

        try:
            login_session.controller.cancel()
        except Exception:
            pass
        self.env.stop()

    def test_server_health_before_initialize(self):
        from aider.z.app_server.handlers import AppServerSession

        s = AppServerSession()
        health = s.handle("server/health", {})
        self.assertTrue(health["ok"])
        self.assertFalse(health["initialized"])
        self.assertIn("pid", health)

    def test_workspace_info_and_open(self):
        from aider.z.app_server.handlers import AppServerSession

        s = AppServerSession()
        s.handle("initialize", {"clientInfo": {"name": "t"}})
        info = s.handle("workspace/info", {})
        self.assertFalse(info["open"])
        out = s.handle("workspace/open", {"root": self.tmp})
        self.assertTrue(out["ok"])
        info2 = s.handle("workspace/info", {})
        self.assertTrue(info2["open"])
        self.assertEqual(info2["root"], str(Path(self.tmp).resolve()))

    def test_auth_login_start_returns_url(self):
        from aider.z.app_server.handlers import AppServerSession
        from aider.z.auth import WebAuthPageSession
        import socketserver

        s = AppServerSession()
        s.handle("initialize", {"clientInfo": {"name": "t"}})

        fake_server = mock.Mock(spec=socketserver.TCPServer)
        result_box = {"data": None, "error": None, "done": __import__("threading").Event()}
        fake = WebAuthPageSession(
            page_url="https://example.test/app/login?state=abc",
            state="abc",
            redirect_uri="http://127.0.0.1:8765/callback",
            result_box=result_box,
            server=fake_server,
        )
        # Don't let background thread block forever — mark done immediately with error
        result_box["error"] = "test-stop"
        result_box["done"].set()

        with mock.patch("aider.z.auth.prepare_web_auth_page", return_value=fake):
            out = s.handle(
                "auth/loginStart",
                {"method": "google", "intent": "signin", "openBrowser": False},
            )
        self.assertTrue(out["started"])
        self.assertEqual(out["status"], "pending")
        self.assertIn("example.test", out["loginUrl"])
        st = s.handle("auth/loginStatus", {})
        self.assertIn(st["status"], ("pending", "failed", "succeeded"))

    def test_auth_logout(self):
        from aider.z.app_server.handlers import AppServerSession

        s = AppServerSession()
        s.handle("initialize", {"clientInfo": {"name": "t"}})
        with mock.patch("aider.z.auth.logout") as logout:
            out = s.handle("auth/logout", {})
        self.assertTrue(out["ok"])
        logout.assert_called_once()


if __name__ == "__main__":
    unittest.main()
