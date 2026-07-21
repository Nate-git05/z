"""P2 terminal UX: compact uncertainty summary, Rich tree, golden noise budget."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from rich.console import Console

_HOME = tempfile.mkdtemp(prefix="z_ux_p2_")
os.environ["Z_HOME"] = _HOME
os.environ.pop("Z_UX_VERBOSE", None)
os.environ.pop("Z_SHOW_USAGE", None)
os.environ.pop("Z_UX_HISTORY_FULL", None)


def _node(title: str, risk: str, *, files=None, ntype=None):
    from aider.z.uncertainty.schema import (
        Area,
        NodeType,
        Tier,
        UncertaintyNode,
    )

    return UncertaintyNode(
        title=title,
        type=ntype or NodeType.EDGE_CASE,
        confidence_tier=Tier.MEDIUM,
        risk_tier=Tier(risk),
        summary=f"summary for {title}",
        explanation="because reasons",
        files_affected=list(files or ["auth/session.py"]),
        suggested_fix="Add a guard",
        area=Area.BACKEND,
    )


def _fresh_store():
    import uuid

    from aider.z.uncertainty.store import UncertaintyStore

    return UncertaintyStore(repo_key=f"p2-ux-{uuid.uuid4().hex}")


class SummaryLineTests(unittest.TestCase):
    def test_compact_high_medium(self):
        from aider.z.uncertainty.ui import format_summary_line

        nodes = [_node("a", "High"), _node("b", "Medium"), _node("c", "Medium")]
        line = format_summary_line(nodes)
        self.assertEqual(line, "Uncertainty · 1 High · 2 Medium — /uncertainties")
        self.assertNotIn("new node", line.lower())
        self.assertNotIn("tree:", line.lower())

    def test_low_only(self):
        from aider.z.uncertainty.ui import format_summary_line

        line = format_summary_line([_node("a", "Low"), _node("b", "Low")])
        self.assertEqual(line, "Uncertainty · 2 Low — /uncertainties")

    def test_empty(self):
        from aider.z.uncertainty.ui import format_summary_line, print_summary_line

        self.assertEqual(format_summary_line([]), "")
        outputs = []
        io_mock = SimpleNamespace(tool_output=lambda *a, **k: outputs.append(a[0] if a else ""))
        print_summary_line(io_mock, [])
        self.assertEqual(outputs, [])

    def test_verbose_appends_count(self):
        from aider.z.uncertainty.ui import format_summary_line

        line = format_summary_line([_node("a", "High")], verbose=True)
        self.assertIn("(1 new)", line)

    def test_print_summary_line_uses_compact(self):
        from aider.z.uncertainty.ui import print_summary_line

        outputs = []
        io_mock = SimpleNamespace(tool_output=lambda *a, **k: outputs.append(a[0] if a else ""))
        print_summary_line(io_mock, [_node("a", "High"), _node("b", "Medium")])
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0], "Uncertainty · 1 High · 1 Medium — /uncertainties")


class RichTreeTests(unittest.TestCase):
    def test_render_tree_pretty_smoke(self):
        from aider.z.uncertainty.ui import render_tree_rich, rows_for_listing

        store = _fresh_store()
        store.add(_node("Risky auth", "High", files=["auth.py"]))
        store.add(_node("Mild cache", "Medium", files=["cache.py"]))
        store.add(_node("Style", "Low", files=["style.py"]))

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=100)
        rows = render_tree_rich(store, console, mode="risk")
        out = buf.getvalue()
        self.assertIn("Risky auth", out)
        self.assertIn("High", out)
        self.assertIn("Medium", out)
        self.assertIn("Low", out)
        # High section appears before Low in output
        self.assertLess(out.find("High"), out.find("Low"))
        self.assertEqual(len(rows), 3)
        # Numbering matches rows_for_listing
        self.assertEqual([n.title for _, n in rows_for_listing(store)], [r[1].title for r in rows])

    def test_render_detail_rich_smoke(self):
        from aider.z.uncertainty.ui import render_detail_rich

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=100)
        render_detail_rich(_node("Risky auth", "High"), console)
        out = buf.getvalue()
        self.assertIn("Risky auth", out)
        self.assertIn("Suggested fix", out)
        self.assertIn("guard", out.lower())

    def test_plain_listing_has_tiers(self):
        from aider.z.uncertainty.ui import render_tree_listing

        store = _fresh_store()
        store.add(_node("Risky auth", "High"))
        store.add(_node("Mild", "Low"))
        text = render_tree_listing(store, mode="risk", color=False)
        self.assertIn("High", text)
        self.assertIn("Risky auth", text)
        self.assertIn("Select #", text)


class BrowseActionsTests(unittest.TestCase):
    def test_browse_ignore_invokes_apply_action(self):
        from aider.z.uncertainty.ui import browse_interactive

        store = _fresh_store()
        node = _node("Risky auth", "High")
        store.add(node)

        answers = iter(["1", "i", ""])  # open #1, ignore, exit
        io = MagicMock()
        io.pretty = False
        io.console = None
        io.prompt_ask = lambda *a, **k: next(answers)
        io.tool_output = MagicMock()
        io.tool_warning = MagicMock()

        browse_interactive(io, store, mode="risk")
        refreshed = store.get(node.id)
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed.status.value, "Ignored")


class GoldenImplementTurnTests(unittest.TestCase):
    """P2.B — synthetic quiet implement-turn noise budget."""

    DENYLIST = (
        "Tokens:",
        "Cost:",
        "Uncertainty tree:",
        "Tracking checklist",
        "new node(s)",
    )

    def test_golden_implement_turn_status_budget(self):
        from aider.z.uncertainty.ui import print_summary_line
        from aider.z.ux_flags import show_usage_enabled
        from aider.z.ux_preamble import TurnPreamble

        status: list[str] = []
        warnings: list[str] = []

        class RecIO:
            pretty = False
            verbose = False
            show_cost = False
            console = None

            def tool_output(self, *messages, **kwargs):
                if messages:
                    status.append(" ".join(map(str, messages)).strip())

            def tool_warning(self, message="", **kwargs):
                if message:
                    warnings.append(str(message).strip())

        io = RecIO()
        pre = TurnPreamble(verbose=False)
        pre.note_skills([], capability_only=True)
        pre.note_explore(2)
        pre.note_plan(gated=True, approved=True)
        pre.flush(io)

        # Compact confirm path leaves no full-plan wall (nothing recorded here)
        print_summary_line(io, [_node("a", "High"), _node("b", "Medium")])

        # Usage must stay silent by default
        coder = MagicMock()
        coder.usage_report = "Tokens: 10 sent, 20 received."
        coder.message_tokens_sent = 10
        coder.message_tokens_received = 20
        coder.message_cost = 0.0
        coder.total_cost = 0.0
        coder.total_tokens_sent = 0
        coder.total_tokens_received = 0
        coder.edit_format = "diff"
        coder.main_model = MagicMock()
        coder.show_cost = False
        coder.io = io
        coder.event = MagicMock()
        from aider.coders.base_coder import Coder

        # Bind unbound method
        Coder.show_usage_report(coder)
        self.assertFalse(show_usage_enabled(coder=coder, io=io))

        # Budget: ≤2 preamble status lines before summary; +1 summary
        # preamble flush is 1 line; summary is 1 line
        self.assertLessEqual(len(status), 3)
        self.assertGreaterEqual(len(status), 2)
        self.assertTrue(any(s.startswith("Uncertainty ·") for s in status))
        self.assertTrue(any("Planning" in s or "explore" in s for s in status))

        joined = "\n".join(status + warnings)
        for bad in self.DENYLIST:
            self.assertNotIn(bad, joined)

    def test_golden_history_omits_status(self):
        from aider.io import InputOutput
        from aider.z.ux_preamble import TurnPreamble

        with tempfile.TemporaryDirectory() as td:
            hist = Path(td) / "chat.md"
            io = InputOutput(
                pretty=False,
                yes=True,
                fancy_input=False,
                input_history_file=None,
                chat_history_file=str(hist),
            )
            pre = TurnPreamble(verbose=False)
            pre.note_skills(["demo"], capability_only=False)
            pre.note_explore(1)
            pre.flush(io)
            text = hist.read_text(encoding="utf-8") if hist.exists() else ""
            # P1: T1 status is not blockquoted into history
            self.assertNotIn("> Planning", text)
            self.assertNotIn("> 1 skill", text)


if __name__ == "__main__":
    unittest.main()
