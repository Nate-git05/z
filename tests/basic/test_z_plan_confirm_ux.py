"""Plan confirm UX: show real approach/steps, not the raw user request."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_plan_ux_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.plan import (  # noqa: E402
    draft_plan_from_request,
    format_plan_for_confirm,
    format_plan_for_user,
)


class PlanConfirmUxTest(unittest.TestCase):
    def test_rpg_plan_has_real_steps_not_raw_request(self):
        msg = "hello lets buold out a game text rpg game where you are in the woods"
        plan = draft_plan_from_request(msg, title=msg[:40], reason="architecture_review")
        self.assertIn("Text RPG", plan.title)
        self.assertNotIn("hello lets", plan.title.lower())
        self.assertTrue(plan.approach)
        self.assertGreaterEqual(len(plan.steps), 4)
        self.assertTrue(any("command" in s.lower() or "parser" in s.lower() for s in plan.steps))

        confirm = format_plan_for_confirm(plan)
        self.assertIn("Steps:", confirm)
        self.assertNotIn("hello lets buold", confirm.lower())
        self.assertIn("1.", confirm)

        full = format_plan_for_user(plan)
        self.assertIn("Approach:", full)
        self.assertIn("Steps:", full)

    def test_confirm_never_equals_raw_title_dump(self):
        msg = "please build a multiplayer lobby with challenges"
        plan = draft_plan_from_request(msg, title=msg)
        confirm = format_plan_for_confirm(plan)
        # Must contain structured steps, not just echo
        self.assertIn("Steps:", confirm)
        self.assertGreaterEqual(confirm.count("\n"), 4)


if __name__ == "__main__":
    unittest.main()
