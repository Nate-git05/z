"""Plan interview, tool-output beyond shell, thin tool-loop."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_ptl_")
os.environ["Z_HOME"] = _HOME


class PlanInterviewTests(unittest.TestCase):
    def setUp(self):
        os.environ["Z_PLAN_INTERVIEW"] = "1"
        os.environ["Z_HOME"] = _HOME

    def test_stages_and_reminder(self):
        from aider.z.plan_interview import (
            PlanInterviewStage,
            advance_after_user_reply,
            detect_stage,
            format_interview_reminder,
            format_status,
        )
        from aider.z.plan_mode import plans_dir

        self.assertEqual(
            advance_after_user_reply(PlanInterviewStage.CLARIFY),
            PlanInterviewStage.DRAFT,
        )
        self.assertEqual(detect_stage(), PlanInterviewStage.CLARIFY)

        path = plans_dir() / "demo.md"
        path.write_text("# Plan\n\n## Steps\n1. Do the thing carefully.\n", encoding="utf-8")
        self.assertEqual(
            detect_stage(active_path=str(path)), PlanInterviewStage.READY
        )
        rem = format_interview_reminder(PlanInterviewStage.CLARIFY, plan_path=path)
        self.assertIn("clarify", rem.lower())
        self.assertIn("Plan mode", rem)
        st = format_status(PlanInterviewStage.DRAFT, plan_path=str(path))
        self.assertIn("draft", st)


class ToolOutputBeyondShellTests(unittest.TestCase):
    def test_inject_budgets_large(self):
        from aider.z.output_budget import inject_tool_result
        from aider.z.mcp_client import format_mcp_result_for_chat

        os.environ["Z_TOOL_OUTPUT_BUDGET"] = "1"
        os.environ["Z_TOOL_OUTPUT_MAX_LINES"] = "50"
        os.environ["Z_TOOL_OUTPUT_MAX_BYTES"] = "2048"
        big = "line\n" * 400
        out = inject_tool_result(big, label="web", command="scrape")
        self.assertIn("Output of", out)
        self.assertIn("budgeted", out.lower())
        self.assertLess(len(out), len(big))

        mcp = format_mcp_result_for_chat("browser", big)
        self.assertIn("mcp:browser", mcp)
        self.assertIn("budgeted", mcp.lower())


class ToolLoopTests(unittest.TestCase):
    def setUp(self):
        os.environ["Z_TOOL_LOOP"] = "1"
        os.environ["Z_HOME"] = _HOME

    def test_extract_and_run_read(self):
        from aider.z.tool_loop import extract_tool_calls, run_tool_loop

        text = """
I'll inspect the file first.

```z-tool
read calcpkg/ops.py
grep average --glob '*.py'
```
"""
        calls = extract_tool_calls(text)
        self.assertEqual([c.name for c in calls], ["read", "grep"])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "calcpkg"
            pkg.mkdir()
            (pkg / "ops.py").write_text(
                "def average(xs):\n    return sum(xs)/len(xs)\n", encoding="utf-8"
            )
            res = run_tool_loop(text, root=root)
            self.assertTrue(res.ran)
            self.assertIn("Tool-loop results", res.reflect_message)
            self.assertIn("average", res.reflect_message)

    def test_disabled(self):
        from aider.z.tool_loop import run_tool_loop

        os.environ["Z_TOOL_LOOP"] = "0"
        try:
            res = run_tool_loop("```z-tool\nread x.py\n```", root=".")
            self.assertFalse(res.ran)
        finally:
            os.environ["Z_TOOL_LOOP"] = "1"

    def test_rejects_path_escape(self):
        from aider.z.tool_loop import run_tool

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = run_tool(
                root,
                __import__("aider.z.tool_loop", fromlist=["ToolCall"]).ToolCall(
                    "read", "../secret"
                ),
            )
            self.assertIn("error", out.lower())


if __name__ == "__main__":
    unittest.main()
