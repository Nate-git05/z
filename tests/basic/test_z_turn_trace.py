"""Turn trace tracker + reasoning divert (Phase 2)."""

from __future__ import annotations

import unittest


class TurnTraceTrackerTests(unittest.TestCase):
    def setUp(self):
        from aider.z.app_server.turn_trace import TurnTraceTracker

        self.events = []
        self.tr = TurnTraceTracker(
            notify=lambda m, p: self.events.append((m, p)),
            turn_id_provider=lambda: "t1",
        )

    def test_thinking_buffers_until_close(self):
        self.tr.open_thinking()
        self.tr.append_reasoning("The request is vague about calculus.\n")
        self.tr.append_reasoning("I should ask for the actual problem.")
        self.assertEqual(self.events, [])
        self.tr.close_thinking_if_open()
        self.assertEqual(len(self.events), 1)
        method, payload = self.events[0]
        self.assertEqual(method, "turn/step")
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["kind"], "thinking")
        self.assertIn("vague", payload["title"].lower() + (payload.get("excerpt") or "").lower())
        self.assertEqual(payload["resolutionLabel"], "Done")

    def test_answer_closes_thinking(self):
        self.tr.append_reasoning("Need to inspect chatPanel.")
        # simulate answer path
        self.tr.close_thinking_if_open()
        self.assertEqual(self.events[-1][1]["status"], "done")

    def test_tool_lines(self):
        self.tr.observe_tool_line("Running rg Contemplating --glob '*.ts'")
        self.assertEqual(self.events[-1][1]["kind"], "search")
        self.tr.observe_tool_line("Running pytest tests/basic -q")
        self.assertEqual(self.events[-1][1]["kind"], "shell")
        self.tr.observe_tool_line("## `read apps/z-desktop/extension/src/chatPanel.ts`")
        self.assertEqual(self.events[-1][1]["kind"], "read")
        self.assertIn("chatPanel", self.events[-1][1]["title"])

    def test_edit_and_mcp(self):
        self.tr.note_edit(["apps/z-desktop/extension/src/chatPanel.ts"], lines_added=3, lines_removed=1)
        self.assertEqual(self.events[-1][1]["kind"], "edit")
        self.tr.note_mcp_started(server="github", tool="search", call_id="c1")
        n = len(self.events)
        self.tr.note_mcp_finished(
            server="github", tool="search", call_id="c1", ok=True, summary="3 hits", duration_ms=12
        )
        self.assertEqual(len(self.events), n + 1)
        self.assertEqual(self.events[-1][1]["kind"], "mcp")
        self.assertEqual(self.events[-1][1]["status"], "done")

    def test_waiting_marks_needs_input(self):
        self.tr.append_reasoning("Unclear what to build next.")
        self.tr.mark_waiting(kind="plan_confirm", question="Approve this plan?")
        self.assertEqual(self.events[-1][1]["status"], "needs_input")
        self.assertEqual(self.events[-1][1]["resolutionLabel"], "Needs input")

    def test_finalize_cancels_open_thinking(self):
        self.tr.append_reasoning("Still thinking…")
        self.tr.finalize(ok=False, interrupted=True)
        self.assertEqual(self.events[-1][1]["status"], "cancelled")

    def test_applied_edit_echo_ignored(self):
        self.tr.observe_tool_line("Applied edit to foo.py")
        self.assertEqual(self.events, [])


class ReasoningStripTests(unittest.TestCase):
    def test_strip_markers(self):
        from aider.reasoning_tags import REASONING_END, REASONING_START
        from aider.z.app_server.io_bridge import _strip_reasoning_markers

        raw = f"{REASONING_START}\nthought\n{REASONING_END}\nHello"
        out = _strip_reasoning_markers(raw)
        self.assertIn("Hello", out)
        self.assertNotIn("THINKING", out)
        self.assertNotIn("ANSWER", out)


if __name__ == "__main__":
    unittest.main()
