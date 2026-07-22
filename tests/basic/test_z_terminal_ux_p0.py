"""P0 terminal UX: colors, compact confirm+View, quiet preamble."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_HOME = tempfile.mkdtemp(prefix="z_ux_p0_")
os.environ["Z_HOME"] = _HOME
os.environ.pop("Z_UX_VERBOSE", None)
os.environ.pop("Z_UX_FULL_PLAN_FIRST", None)
os.environ.pop("Z_UX_PREAMBLE", None)


class ThemeTierTests(unittest.TestCase):
    def test_status_not_warning(self):
        from aider.z.theme import ACCENT, STATUS, TOOL_OUTPUT, TOOL_WARNING

        self.assertEqual(TOOL_OUTPUT, STATUS)
        self.assertEqual(TOOL_OUTPUT, "#9C948A")
        self.assertEqual(TOOL_WARNING, ACCENT)
        self.assertNotEqual(TOOL_OUTPUT, TOOL_WARNING)


class PreambleTests(unittest.TestCase):
    def test_flush_silent_by_default(self):
        from aider.z.ux_preamble import TurnPreamble

        outputs = []
        io = SimpleNamespace(tool_output=lambda *a, **k: outputs.append(" ".join(map(str, a))))
        pre = TurnPreamble(verbose=False)
        pre.note_skills([], capability_only=True)
        pre.note_explore(2)
        pre.note_plan(gated=True, approved=True)
        pre.flush(io)
        self.assertEqual(outputs, [])
        # second flush is still a no-op
        pre.flush(io)
        self.assertEqual(outputs, [])

    def test_flush_one_line_when_preamble_escape(self):
        from aider.z.ux_preamble import TurnPreamble

        outputs = []
        io = SimpleNamespace(tool_output=lambda *a, **k: outputs.append(" ".join(map(str, a))))
        pre = TurnPreamble(verbose=False)
        pre.note_skills([], capability_only=True)
        pre.note_explore(2)
        pre.note_plan(gated=True, approved=True)
        with patch.dict(os.environ, {"Z_UX_PREAMBLE": "1"}):
            pre.flush(io)
        self.assertEqual(len(outputs), 1)
        self.assertIn("Planning", outputs[0])
        self.assertIn("explore 2", outputs[0])
        self.assertIn("plan approved", outputs[0])

    def test_verbose_skips_flush(self):
        from aider.z.ux_preamble import TurnPreamble

        outputs = []
        io = SimpleNamespace(tool_output=lambda *a, **k: outputs.append("x"))
        pre = TurnPreamble(verbose=True)
        pre.note_explore(3)
        pre.flush(io)
        self.assertEqual(outputs, [])


class CompactConfirmTests(unittest.TestCase):
    def test_thin_confirm_has_tracking_not_wall_header(self):
        from aider.z.uncertainty.plan import draft_plan_from_request, format_thin_confirm
        from aider.z.uncertainty.schema import RequirementItem, TaskChecklist

        plan = draft_plan_from_request(
            "build a thread-safe event bus in C++",
            reason="architecture_review",
        )
        cl = TaskChecklist(
            task_id="t1",
            title="Event bus",
            items=[RequirementItem(text="Add bounded per-subscriber queues", kind="product")],
        )
        body = format_thin_confirm(plan, cl)
        self.assertIn("Steps:", body)
        self.assertIn("Tracking:", body)
        self.assertNotIn("Tracking checklist (confirm or correct", body)

    def test_view_then_yes(self):
        from aider.z.uncertainty.plan import draft_plan_from_request, interactive_plan_confirm

        plan = draft_plan_from_request(
            "Add a REST endpoint for users",
            reason="architecture_review",
        )
        outputs = []
        choices = iter(["view", "yes"])

        class IO:
            yes = None

            def plan_confirm_ask(self, question, subject=None, default="y"):
                return next(choices)

            def tool_output(self, *a, **k):
                outputs.append(" ".join(str(x) for x in a) if a else "")

            def tool_warning(self, *a, **k):
                pass

            def confirm_ask(self, *a, **k):
                return True

        ok, out = interactive_plan_confirm(IO(), plan, original_request="Add a REST endpoint")
        self.assertTrue(ok)
        blob = "\n".join(outputs)
        self.assertIn("Implementation plan:", blob)
        self.assertIn("Named invariants:", blob)

    def test_change_does_not_dump_full_plan(self):
        from aider.z.uncertainty.plan import draft_plan_from_request, interactive_plan_confirm

        plan = draft_plan_from_request(
            "Add a REST endpoint for users",
            reason="architecture_review",
        )
        outputs = []
        choices = iter(["change", "yes"])

        class IO:
            yes = None
            _pending_plan_change = "use FastAPI and skip websockets"

            def plan_confirm_ask(self, question, subject=None, default="y"):
                return next(choices)

            def tool_output(self, *a, **k):
                outputs.append(" ".join(str(x) for x in a) if a else "")

            def tool_warning(self, *a, **k):
                pass

            def prompt_ask(self, *a, **k):
                return "use FastAPI"

            def confirm_ask(self, *a, **k):
                return True

        ok, out = interactive_plan_confirm(IO(), plan, original_request="Add a REST endpoint")
        self.assertTrue(ok)
        blob = "\n".join(outputs)
        self.assertNotIn("Named invariants:", blob)
        self.assertIn("Revising plan", blob)


class NoPreDumpCoderTests(unittest.TestCase):
    def test_require_plan_skips_full_dump_by_default(self):
        from aider.coders.base_coder import Coder
        from aider.z.uncertainty.plan import PlanningArtifact

        outputs = []
        io = SimpleNamespace(
            yes=True,
            tool_output=lambda *a, **k: outputs.append(" ".join(map(str, a)) if a else ""),
            tool_warning=lambda *a, **k: None,
        )
        plan = PlanningArtifact(
            task_id="t1",
            title="Event bus",
            reason="architecture_review",
            approach="Use mutex + queues",
            steps=["Define API", "Add tests"],
            approved=False,
            skipped=False,
        )
        eng = MagicMock()
        eng.maybe_require_plan.return_value = plan
        eng.approve_plan = MagicMock()
        eng.record_user_decision = MagicMock()
        eng.ctx = SimpleNamespace(plan=None, plan_approved=False, plan_required=False)

        coder = MagicMock(spec=Coder)
        coder.io = io
        coder.verbose = False
        coder.root = "."
        coder.cur_messages = []
        coder._turn_preamble = None
        coder._phase_spinner_update = lambda *a, **k: None
        coder._phase_spinner_stop = lambda *a, **k: None
        coder.uncertainty_engine = eng
        coder.get_inchat_relative_files = lambda: []

        ok = Coder._maybe_require_implementation_plan(coder, "Implement thread-safe event bus")
        self.assertTrue(ok)
        blob = "\n".join(outputs)
        self.assertNotIn("Named invariants:", blob)
        self.assertIn("Plan approved", blob)


if __name__ == "__main__":
    unittest.main()
