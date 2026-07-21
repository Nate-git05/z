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
        # Must not keep a single echo item
        joined = " ".join(i.text.lower() for i in cl.items)
        self.assertNotEqual(joined.strip(), msg)
        self.assertTrue(
            any("socket" in i.text.lower() or "webhook" in i.text.lower() for i in cl.items)
            or any("scaffold" in i.text.lower() for i in cl.items)
        )
        # Preview UI shows approach + steps, not only the echo
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


class BeginTaskConfirmTest(unittest.TestCase):
    def test_thin_task_shows_confirm_subject_with_steps(self):
        """Interactive thin preview uses confirm_ask with real steps."""
        from aider.coders.base_coder import Coder

        io = MagicMock()
        io.yes = None
        io.confirm_ask.return_value = True

        # Minimal coder stub — call the method directly
        coder = MagicMock(spec=Coder)
        coder.io = io
        coder._drift_asked_this_task = False
        coder._drift_reflection_log = []

        from aider.z.uncertainty.engine import SessionContext, UncertaintyEngine
        from aider.z.uncertainty.store import UncertaintyStore
        from pathlib import Path

        store = UncertaintyStore(root=Path(_HOME) / "uncertainties")
        ctx = SessionContext(root=Path(_HOME), store=store)
        engine = UncertaintyEngine(ctx)
        coder.uncertainty_engine = engine

        # Bind real method
        Coder._maybe_begin_uncertainty_task(coder, "build me a slack chatbot")

        self.assertTrue(io.tool_output.called)
        printed = "\n".join(
            str(c.args[0]) for c in io.tool_output.call_args_list if c.args
        )
        self.assertIn("Proposed approach", printed)
        self.assertIn("Implementation steps:", printed)
        self.assertTrue(io.confirm_ask.called)
        subject = io.confirm_ask.call_args.kwargs.get("subject") or ""
        self.assertIn("Steps:", subject)
        self.assertNotIn("Do: build me a slack chatbot", subject)
        self.assertTrue(engine.ctx.checklist.confirmed_by_user)


if __name__ == "__main__":
    unittest.main()
