"""Coding-quality tranche 1: compact skills, tool-output budget, strict chat edits."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_HOME = tempfile.mkdtemp(prefix="z_cq_")
os.environ["Z_HOME"] = _HOME


class CompactSkillTests(unittest.TestCase):
    def test_directive_truncates_long_body(self):
        from aider.z.coding_context import format_skill_directive, skill_inject_full_enabled
        from aider.z.skills.schema import Skill

        self.assertFalse(skill_inject_full_enabled())
        long_body = "line\n" * 500
        skill = Skill(
            id="s1",
            title="Stripe webhooks",
            description="How we verify Stripe",
            content=long_body,
            kind="playbook",
            languages=["python"],
            capability="payments.verify",
            path="/tmp/fake-skill.md",
        )
        out = format_skill_directive(skill, body_budget=400)
        self.assertIn("Skill directive", out)
        self.assertIn("Stripe webhooks", out)
        self.assertIn("truncated", out.lower())
        self.assertLess(len(out), len(long_body))
        self.assertIn("full_skill_path=", out)

    def test_format_skills_for_context_uses_compact(self):
        from aider.z.skills.schema import Skill
        from aider.z.skills.session import format_skills_for_context

        skill = Skill(
            id="s2",
            title="Auth playbook",
            description="session cookies",
            content=("RULE\n" * 300),
            kind="playbook",
            path="/tmp/auth.md",
        )
        compact = format_skills_for_context([skill])
        self.assertIn("Compact skill directives", compact)
        self.assertIn("truncated", compact.lower())
        self.assertLess(len(compact), len(skill.content))

        os.environ["Z_SKILL_INJECT_FULL"] = "1"
        try:
            full = format_skills_for_context([skill])
            self.assertIn("RULE", full)
            self.assertGreater(len(full), len(compact))
        finally:
            os.environ.pop("Z_SKILL_INJECT_FULL", None)


class OutputBudgetTests(unittest.TestCase):
    def test_small_output_unchanged(self):
        from aider.z.output_budget import budget_tool_output

        text = "ok\npass\n"
        out, path = budget_tool_output(text, label="t")
        self.assertEqual(out, text)
        self.assertIsNone(path)

    def test_large_output_persisted(self):
        from aider.z.output_budget import budget_tool_output, tool_output_dir

        os.environ["Z_HOME"] = _HOME
        lines = [f"line-{i}\n" for i in range(5000)]
        text = "".join(lines)
        out, path = budget_tool_output(text, label="pytest", lines_limit=100, bytes_limit=4096)
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).is_file())
        self.assertIn("tool-output budgeted", out)
        self.assertIn(str(path), out)
        self.assertLess(len(out), len(text))
        self.assertEqual(Path(path).read_text(encoding="utf-8"), text)
        self.assertEqual(Path(path).parent, tool_output_dir())
        self.assertIn("tool-output", str(path))


class StrictChatEditTests(unittest.TestCase):
    def test_blocks_existing_file_not_in_chat(self):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput
        from aider.models import Model

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "existing.py"
            target.write_text("x = 1\n", encoding="utf-8")

            io = InputOutput(yes=True)  # would auto-approve legacy confirm
            model = Model("gpt-4o-mini")
            coder = Coder.create(
                main_model=model,
                io=io,
                fnames=[],
                edit_format="diff",
            )
            coder.root = str(root)
            coder.repo = None  # avoid git path_in_repo against workspace
            coder.abs_fnames = set()

            os.environ["Z_STRICT_CHAT_EDITS"] = "1"
            try:
                allowed = coder.allowed_to_edit("existing.py")
                self.assertFalse(bool(allowed))
                self.assertIn("not in the chat", coder.reflected_message or "")
            finally:
                os.environ.pop("Z_STRICT_CHAT_EDITS", None)

    def test_allows_when_already_in_chat(self):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput
        from aider.models import Model

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "inchat.py"
            target.write_text("y = 2\n", encoding="utf-8")

            io = InputOutput(yes=True)
            model = Model("gpt-4o-mini")
            coder = Coder.create(
                main_model=model,
                io=io,
                fnames=[],
                edit_format="diff",
            )
            coder.root = str(root)
            coder.repo = None
            abs_t = str(target.resolve())
            coder.abs_fnames = {abs_t}

            os.environ["Z_STRICT_CHAT_EDITS"] = "1"
            try:
                # allowed_to_edit resolves via abs_root_path / path_under_root
                allowed = coder.allowed_to_edit("inchat.py")
                self.assertTrue(bool(allowed))
            finally:
                os.environ.pop("Z_STRICT_CHAT_EDITS", None)

    def test_strict_flag_helpers(self):
        from aider.z.coding_context import strict_chat_edits_enabled

        os.environ.pop("Z_STRICT_CHAT_EDITS", None)
        self.assertTrue(strict_chat_edits_enabled())
        os.environ["Z_STRICT_CHAT_EDITS"] = "0"
        try:
            self.assertFalse(strict_chat_edits_enabled())
        finally:
            os.environ.pop("Z_STRICT_CHAT_EDITS", None)


if __name__ == "__main__":
    unittest.main()
