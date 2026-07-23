"""Coding quality tranche 2: PLAN mode, explore pass, done soft-stop."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_HOME = tempfile.mkdtemp(prefix="z_cq2_")
os.environ["Z_HOME"] = _HOME


class PlanModeTests(unittest.TestCase):
    def test_task_mode_plan_policies(self):
        from aider.z.task_mode import TaskMode, classify_task_mode

        self.assertFalse(TaskMode.PLAN.allows_edits)
        self.assertTrue(TaskMode.PLAN.allows_plan_file_edits)
        self.assertTrue(TaskMode.PLAN.allows_planning)
        self.assertFalse(TaskMode.PLAN.allows_shell_mutation)
        self.assertEqual(classify_task_mode("plan", "anything"), TaskMode.PLAN)

    def test_plan_blocks_product_allows_artifact(self):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput
        from aider.models import Model
        from aider.z.plan_mode import plans_dir
        from aider.z.task_mode import TaskMode

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            product = root / "app.py"
            product.write_text("x=1\n", encoding="utf-8")

            io = InputOutput(yes=True)
            coder = Coder.create(
                main_model=Model("gpt-4o-mini"),
                io=io,
                fnames=[],
                edit_format="diff",
            )
            coder.root = str(root)
            coder.repo = None
            coder.abs_fnames = set()
            coder.task_mode = TaskMode.PLAN

            self.assertFalse(bool(coder.allowed_to_edit("app.py")))

            plan_name = "demo-plan.md"
            allowed = coder.allowed_to_edit(plan_name)
            self.assertTrue(bool(allowed))
            self.assertTrue((plans_dir() / plan_name).exists() or any(
                plan_name in str(p) for p in coder.abs_fnames
            ))


class InvestigateModeTests(unittest.TestCase):
    def test_investigate_blocks_edit_to_disk(self):
        """A turn classified INVESTIGATE (e.g. 'do not edit any files') must be
        refused at the point edits are actually applied, not just nudged via
        a system-prompt reminder the model is free to ignore."""
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput
        from aider.models import Model
        from aider.z.task_mode import TaskMode

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            product = root / "app.py"
            product.write_text("x=1\n", encoding="utf-8")

            io = InputOutput(yes=True)
            coder = Coder.create(
                main_model=Model("gpt-4o-mini"),
                io=io,
                fnames=[],
                edit_format="diff",
            )
            coder.root = str(root)
            coder.repo = None
            coder.abs_fnames = set()
            coder.task_mode = TaskMode.INVESTIGATE

            self.assertFalse(bool(coder.allowed_to_edit("app.py")))
            self.assertEqual(coder.abs_fnames, set())


class AskModeReminderTests(unittest.TestCase):
    """A plain greeting/question auto-classified TaskMode.ASK must not get the
    file-editing system prompt framing that hallucinates a coding task."""

    def _make_coder(self, edit_format="diff"):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput
        from aider.models import Model

        io = InputOutput(yes=True)
        coder = Coder.create(
            main_model=Model("gpt-4o-mini"),
            io=io,
            fnames=[],
            edit_format=edit_format,
        )
        coder.root = tempfile.mkdtemp(prefix="z_ask_reminder_")
        coder.repo = None
        coder.abs_fnames = set()
        return coder

    def test_ask_mode_gets_conversational_reminder(self):
        from aider.z.task_mode import TaskMode

        coder = self._make_coder(edit_format="diff")
        coder.task_mode = TaskMode.ASK
        prompt = coder.fmt_system_prompt(coder.gpt_prompts.main_system)
        self.assertIn("This turn is conversational", prompt)
        self.assertNotIn("# Coding quality (Z)", prompt)

    def test_implement_mode_has_no_conversational_reminder(self):
        from aider.z.task_mode import TaskMode

        coder = self._make_coder(edit_format="diff")
        coder.task_mode = TaskMode.IMPLEMENT
        prompt = coder.fmt_system_prompt(coder.gpt_prompts.main_system)
        self.assertNotIn("This turn is conversational", prompt)

    def test_explicit_ask_command_skips_duplicate_reminder(self):
        """Explicit /ask already uses AskPrompts with no edit framing at all —
        no need to also inject the diff-format counter-instruction."""
        from aider.z.task_mode import TaskMode

        coder = self._make_coder(edit_format="ask")
        coder.task_mode = TaskMode.ASK
        prompt = coder.fmt_system_prompt(coder.gpt_prompts.main_system)
        self.assertNotIn("This turn is conversational", prompt)


class ExplorePassTests(unittest.TestCase):
    def test_extract_and_find(self):
        from aider.z.explore import extract_keywords, run_explore_pass

        kws = extract_keywords("Fix average() off-by-one in calcpkg/ops.py")
        self.assertTrue(any("average" in k.lower() or "ops" in k.lower() for k in kws))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "calcpkg"
            pkg.mkdir()
            (pkg / "ops.py").write_text("def average(xs): return sum(xs)/len(xs)\n", encoding="utf-8")
            os.environ["Z_EXPLORE_PASS"] = "1"
            os.environ["Z_EXPLORE_DEPTH"] = "thin"
            block = run_explore_pass(
                "investigate average in calcpkg ops",
                root=root,
                already_in_chat=[],
            )
            self.assertIn("Explore", block)
            self.assertIn("ops.py", block)


class DoneSoftStopTests(unittest.TestCase):
    def test_claim_detection_and_reason(self):
        from aider.z.uncertainty.done_gate import (
            looks_like_done_claim,
            soft_stop_reason,
        )

        self.assertTrue(looks_like_done_claim("All done — the bug is fixed."))
        self.assertFalse(looks_like_done_claim("Looking into the bug next."))
        reason = soft_stop_reason(open_high_count=2, last_verify_failed=True)
        self.assertIsNotNone(reason)
        self.assertIn("High uncertainty", reason)
        self.assertIsNone(soft_stop_reason(open_high_count=0, last_verify_failed=False))


if __name__ == "__main__":
    unittest.main()
