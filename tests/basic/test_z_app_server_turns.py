"""Phase 4 — AppServerIO waiting_input bridge + turn respond wiring."""

from __future__ import annotations

import threading
import unittest
from unittest import mock


class AppServerIOBridgeTest(unittest.TestCase):
    def test_confirm_ask_waits_for_respond(self):
        from aider.z.app_server.io_bridge import AppServerIO

        notes = []

        def notify(method, params):
            notes.append((method, params))

        io = AppServerIO(notify=notify, turn_id_provider=lambda: "turn-1", root=".")
        result_box = {}

        def worker():
            result_box["ok"] = io.confirm_ask("Run shell command?", default="n")

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Wait until waiting_input notification appears
        for _ in range(50):
            if any(m == "turn/waiting_input" for m, _ in notes):
                break
            t.join(0.05)
        waiting = [p for m, p in notes if m == "turn/waiting_input"]
        self.assertTrue(waiting)
        request_id = waiting[0]["requestId"]
        self.assertTrue(io.deliver_response(request_id, "yes"))
        t.join(timeout=2)
        self.assertTrue(result_box.get("ok"))

    def test_plan_confirm_change_with_text(self):
        from aider.z.app_server.io_bridge import AppServerIO

        notes = []
        io = AppServerIO(
            notify=lambda m, p: notes.append((m, p)),
            turn_id_provider=lambda: "t",
            root=".",
        )
        box = {}

        def worker():
            box["choice"] = io.plan_confirm_ask("Approve plan?", subject="## Plan\n1. A")

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        for _ in range(50):
            if any(m == "turn/waiting_input" for m, _ in notes):
                break
            t.join(0.05)
        rid = [p for m, p in notes if m == "turn/waiting_input"][0]["requestId"]
        io.deliver_response(rid, "change", text="use rust instead")
        t.join(timeout=2)
        self.assertEqual(box.get("choice"), "change")
        self.assertEqual(getattr(io, "_pending_plan_change", None), "use rust instead")

    def test_emit_llm_delta_notifies(self):
        from aider.z.app_server.io_bridge import AppServerIO

        notes = []
        io = AppServerIO(
            notify=lambda m, p: notes.append((m, p)),
            turn_id_provider=lambda: "t",
            root=".",
        )
        io.emit_llm_delta("Hello")
        deltas = [p for m, p in notes if m == "item/agentMessage/delta"]
        self.assertTrue(deltas)
        self.assertEqual(deltas[0]["text"], "Hello")

    def test_state_change_emits_turn_busy(self):
        from aider.z.app_server.io_bridge import AppServerIO

        notes = []
        io = AppServerIO(
            notify=lambda m, p: notes.append((m, p)),
            turn_id_provider=lambda: "t",
            root=".",
        )
        orch = io.ensure_turn_ux()
        orch.enter_busy("Planning — matching skills…")
        busy = [p for m, p in notes if m == "turn/busy"]
        self.assertTrue(busy)
        self.assertEqual(busy[-1]["state"], "busy")
        self.assertIn("skills", busy[-1]["phase"] or "")


class QueuePreviewNotifyTest(unittest.TestCase):
    def test_queue_change_includes_items_and_preview(self):
        from aider.z.app_server.io_bridge import AppServerIO

        notes = []
        io = AppServerIO(
            notify=lambda m, p: notes.append((m, p)),
            turn_id_provider=lambda: "t",
            root=".",
        )
        orch = io.ensure_turn_ux()
        orch.enter_busy("Working…")
        self.assertTrue(orch.enqueue("follow up: fix the tests"))
        queued = [p for m, p in notes if m == "turn/queued"]
        self.assertTrue(queued)
        last = queued[-1]
        self.assertEqual(last["queueLen"], 1)
        self.assertEqual(last["items"], ["follow up: fix the tests"])
        self.assertIn("queued", (last.get("preview") or "").lower())
        self.assertIn("follow up", last.get("preview") or "")


class TurnManagerRespondTest(unittest.TestCase):
    def test_respond_routes_to_runner_io(self):
        from aider.z.app_server.turn_runner import TurnManager
        from aider.z.app_server.io_bridge import AppServerIO

        notes = []
        mgr = TurnManager(workspace_root="/tmp", notify=lambda m, p: notes.append((m, p)))
        runner = mgr._runner("default")
        runner._io = AppServerIO(
            notify=lambda m, p: None,
            turn_id_provider=lambda: "t",
            root="/tmp",
        )
        runner._io._pending_request_id = "req-1"
        self.assertTrue(mgr.respond(request_id="req-1", response="yes"))
        self.assertFalse(mgr.respond(request_id="missing", response="yes"))


if __name__ == "__main__":
    unittest.main()
