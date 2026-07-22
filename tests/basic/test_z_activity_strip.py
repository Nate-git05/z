"""Activity strip tracker + AppServerIO wiring (turn/activity)."""

from __future__ import annotations

import unittest


class TurnActivityTrackerTest(unittest.TestCase):
    def test_tool_output_parsing_and_payload(self):
        from aider.z.app_server.activity import TurnActivityTracker

        notes = []
        tracker = TurnActivityTracker(
            notify=lambda m, p: notes.append((m, p)),
            turn_id_provider=lambda: "t1",
        )
        tracker.observe_tool_output("Applied edit to foo.py")
        tracker.observe_tool_output("Applied edit to bar.py")
        tracker.observe_tool_output("Running rg TODO")
        tracker.observe_tool_output("Running pytest -q")
        tracker.observe_tool_output("Exploring related files (background)…")
        tracker.observe_tool_output("MCP: github.search ok (12ms)")
        tracker.note_edits(["foo.py"], lines_added=10, lines_removed=3)
        tracker.set_model("openai/gpt-4o")
        tracker.flush(force=True)

        acts = [p for m, p in notes if m == "turn/activity"]
        self.assertTrue(acts)
        last = acts[-1]
        self.assertEqual(last["turnId"], "t1")
        self.assertEqual(last["editingFiles"], 2)
        self.assertGreaterEqual(last["searches"], 1)
        self.assertGreaterEqual(last["commands"], 2)
        self.assertGreaterEqual(last["mcpCalls"], 1)
        self.assertEqual(last["linesAdded"], 10)
        self.assertEqual(last["linesRemoved"], 3)
        self.assertEqual(last["modelId"], "openai/gpt-4o")
        self.assertEqual(last["phase"], "editing")

    def test_line_delta_from_edit(self):
        from aider.z.app_server.activity import line_delta_from_edit

        add, rem = line_delta_from_edit("a\nb\n", "a\nb\nc\n")
        self.assertEqual(add, 3)
        self.assertEqual(rem, 2)

    def test_map_phase_id(self):
        from aider.z.app_server.activity import map_phase_id

        self.assertEqual(map_phase_id("Waiting for model…"), "thinking")
        self.assertEqual(map_phase_id("Planning — matching skills…"), "planning")
        self.assertEqual(map_phase_id("Choosing model"), "choosing_model")


class AppServerIOActivityTest(unittest.TestCase):
    def test_tool_output_emits_turn_activity(self):
        from aider.z.app_server.io_bridge import AppServerIO

        notes = []
        io = AppServerIO(
            notify=lambda m, p: notes.append((m, p)),
            turn_id_provider=lambda: "turn-a",
            root=".",
        )
        io.tool_output("Applied edit to src/a.ts")
        io.activity.flush(force=True)
        acts = [p for m, p in notes if m == "turn/activity"]
        self.assertTrue(acts)
        self.assertEqual(acts[-1]["editingFiles"], 1)
        self.assertEqual(acts[-1]["phase"], "editing")

    def test_llm_started_sets_thinking(self):
        from aider.z.app_server.io_bridge import AppServerIO

        notes = []
        io = AppServerIO(
            notify=lambda m, p: notes.append((m, p)),
            turn_id_provider=lambda: "t",
            root=".",
        )
        io.llm_started()
        acts = [p for m, p in notes if m == "turn/activity"]
        self.assertTrue(acts)
        self.assertEqual(acts[-1]["phase"], "thinking")


if __name__ == "__main__":
    unittest.main()
