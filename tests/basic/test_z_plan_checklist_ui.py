"""Thin checklist / plan preview UI — never echo the raw request as the plan."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock

_HOME = tempfile.mkdtemp(prefix="z_plan_chk_ui_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.checklist import (  # noqa: E402
    checklist_is_thin,
    decompose_request,
    enrich_thin_checklist,
    format_checklist_for_user,
)
from aider.z.uncertainty.plan import (  # noqa: E402
    draft_plan_from_request,
    format_plan_for_confirm,
    interactive_plan_confirm,
    revise_plan_with_feedback,
)


class ThinChecklistUiTest(unittest.TestCase):
    def test_slack_chatbot_is_thin_before_enrich(self):
        msg = "build me a slack chatbot"
        cl = decompose_request("", msg)
        self.assertTrue(checklist_is_thin(cl, msg))
        self.assertEqual(len(cl.items), 1)
        self.assertIn("slack chatbot", cl.items[0].text.lower())

    def test_enrich_replaces_echo_with_real_steps(self):
        msg = "build me a slack chatbot"
        cl = decompose_request("", msg)
        cl, plan, was_thin = enrich_thin_checklist(cl, msg)
        self.assertTrue(was_thin)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.approach)
        self.assertGreaterEqual(len(plan.steps), 4)
        joined = " ".join(i.text.lower() for i in cl.items)
        self.assertNotEqual(joined.strip(), msg)
        self.assertTrue(
            any(
                "socket" in i.text.lower() or "webhook" in i.text.lower()
                for i in cl.items
            )
            or any("scaffold" in i.text.lower() for i in cl.items)
        )
        rendered = format_checklist_for_user(cl, plan=plan, thin=True)
        self.assertIn("Proposed approach", rendered)
        self.assertIn("Implementation steps:", rendered)
        self.assertNotIn("[product] build me a slack chatbot", rendered.lower())
        self.assertIn("Tracking checklist", rendered)

    def test_plan_draft_slack_has_chatbot_template(self):
        msg = "build me a slack chatbot"
        plan = draft_plan_from_request(msg)
        self.assertIn("Chatbot", plan.title)
        confirm = format_plan_for_confirm(plan)
        self.assertIn("Steps:", confirm)
        self.assertNotIn("Do: build me a slack chatbot", confirm)
        self.assertTrue(any("scaffold" in s.lower() for s in plan.steps))

    def test_specific_multi_item_checklist_not_thin(self):
        msg = (
            "1. Add /health endpoint\n"
            "2. Return JSON status ok\n"
            "3. Add a unit test for /health"
        )
        cl = decompose_request("", msg)
        self.assertFalse(checklist_is_thin(cl, msg))
        cl2, plan, was_thin = enrich_thin_checklist(cl, msg)
        self.assertFalse(was_thin)
        self.assertIsNone(plan)
        rendered = format_checklist_for_user(cl2)
        self.assertIn("Task checklist:", rendered)
        self.assertNotIn("Proposed approach", rendered)


class PlanChangeOptionTest(unittest.TestCase):
    def test_revise_socket_mode_feedback(self):
        plan = draft_plan_from_request("build me a slack chatbot")
        revised = revise_plan_with_feedback(
            plan,
            "use socket mode, python, no llm",
            original_request="build me a slack chatbot",
        )
        blob = " ".join(revised.steps).lower() + " " + (revised.approach or "").lower()
        self.assertIn("socket", blob)
        self.assertTrue(
            any("python" in (s or "").lower() for s in revised.steps)
            or "python" in (revised.approach or "").lower()
        )
        self.assertTrue(any("llm" in (o or "").lower() for o in revised.out_of_scope))
        self.assertTrue(
            any(
                "user revised" in (a.ambiguity or "").lower()
                for a in revised.ambiguities
            )
        )

    def test_interactive_change_then_yes(self):
        plan = draft_plan_from_request("build me a slack chatbot")
        io = MagicMock()
        io.yes = None
        io.plan_confirm_ask = MagicMock(side_effect=["change", "yes"])
        io.prompt_ask = MagicMock(return_value="use socket mode and python")
        io._pending_plan_change = None
        io.confirm_ask = MagicMock(return_value=True)

        ok, final = interactive_plan_confirm(
            io,
            plan,
            question="Proceed with this approach?",
            original_request="build me a slack chatbot",
        )
        self.assertTrue(ok)
        self.assertIsNotNone(final)
        self.assertEqual(io.plan_confirm_ask.call_count, 2)
        self.assertTrue(io.prompt_ask.called)
        blob = " ".join(final.steps).lower()
        self.assertIn("socket", blob)

    def test_free_text_pending_change_at_confirm(self):
        plan = draft_plan_from_request("build me a slack chatbot")
        mock_io = MagicMock()
        mock_io.yes = None
        mock_io.plan_confirm_ask = MagicMock(side_effect=["change", "yes"])
        mock_io._pending_plan_change = "webhook only, typescript"
        mock_io.prompt_ask = MagicMock(return_value="")
        mock_io.confirm_ask = MagicMock(return_value=True)

        ok, final = interactive_plan_confirm(
            mock_io,
            plan,
            original_request="build me a slack chatbot",
        )
        self.assertTrue(ok)
        blob = " ".join(final.steps).lower() + (final.approach or "").lower()
        self.assertTrue("webhook" in blob or "typescript" in blob)


class BeginTaskConfirmTest(unittest.TestCase):
    def test_thin_task_uses_plan_confirm_with_change(self):
        from pathlib import Path

        from aider.coders.base_coder import Coder
        from aider.z.uncertainty.engine import SessionContext, UncertaintyEngine
        from aider.z.uncertainty.store import UncertaintyStore

        io = MagicMock()
        io.yes = None
        io.plan_confirm_ask = MagicMock(return_value="yes")
        io.confirm_ask = MagicMock(return_value=True)

        coder = MagicMock(spec=Coder)
        coder.io = io
        coder.cur_messages = []
        coder._drift_asked_this_task = False
        coder._drift_reflection_log = []

        store = UncertaintyStore(root=Path(_HOME) / "uncertainties")
        ctx = SessionContext(root=Path(_HOME), store=store)
        engine = UncertaintyEngine(ctx)
        coder.uncertainty_engine = engine

        Coder._maybe_begin_uncertainty_task(coder, "build me a slack chatbot")

        printed = "\n".join(
            str(c.args[0]) for c in io.tool_output.call_args_list if c.args
        )
        # P0: no checklist wall in scrollback — confirm panel carries the approach
        self.assertNotIn("Proposed approach", printed)
        self.assertTrue(io.plan_confirm_ask.called)
        # Subject passed to confirm should include steps
        kwargs = io.plan_confirm_ask.call_args.kwargs
        subject = kwargs.get("subject") or ""
        self.assertTrue(
            "Steps:" in subject or "Tracking:" in subject or len(subject) > 20,
            subject,
        )
        self.assertTrue(engine.ctx.checklist.confirmed_by_user)


if __name__ == "__main__":
    unittest.main()
