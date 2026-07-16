"""Shell-command confirmation must honor --yes-always (dep-install recovery)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from aider.coders import Coder
from aider.io import InputOutput
from aider.models import Model
from aider.utils import GitTemporaryDirectory


class ShellYesAlwaysTest(unittest.TestCase):
    def test_yes_always_approves_shell_command(self):
        """--yes-always must run suggested shell commands (e.g. pip install)."""
        with GitTemporaryDirectory():
            io = InputOutput(yes=True, pretty=False, fancy_input=False)
            coder = Coder.create(Model("gpt-3.5-turbo"), "diff", io=io)

            with patch("aider.coders.base_coder.run_cmd") as mock_run:
                mock_run.return_value = (0, "Successfully installed freezegun\n")
                out = coder.handle_shell_commands("pip install freezegun", group=None)

            mock_run.assert_called_once()
            self.assertIsNotNone(out)
            self.assertIn("freezegun", out)

    def test_yes_false_skips_with_clear_error(self):
        with GitTemporaryDirectory():
            io = InputOutput(yes=False, pretty=False, fancy_input=False)
            errors = []
            io.tool_error = errors.append
            coder = Coder.create(Model("gpt-3.5-turbo"), "diff", io=io)

            with patch("aider.coders.base_coder.run_cmd") as mock_run:
                out = coder.handle_shell_commands("pip install freezegun", group=None)

            mock_run.assert_not_called()
            self.assertIsNone(out)
            self.assertTrue(any("Shell command not run" in e for e in errors))
            self.assertTrue(any("pip install freezegun" in e for e in errors))

    def test_explicit_yes_still_blocks_yes_always_for_gates(self):
        """Commit-gate style prompts must remain non-bypassable by --yes-always."""
        io = InputOutput(yes=True, pretty=False, fancy_input=False)
        self.assertFalse(io.confirm_ask("Override high risk?", explicit_yes_required=True))


class ConfirmEofTest(unittest.TestCase):
    def test_eof_noninteractive_fails_loud(self):
        io = InputOutput(yes=None, pretty=False, fancy_input=False)
        errors = []
        io.tool_error = errors.append

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = False

        with patch("aider.io.sys.stdin", fake_stdin):
            with patch("builtins.input", side_effect=EOFError):
                result = io.confirm_ask("Run shell command?")

        self.assertFalse(result)
        self.assertTrue(any("no interactive terminal" in e.lower() for e in errors))
        self.assertTrue(any("--yes-always" in e for e in errors))

    def test_eof_interactive_uses_default_yes(self):
        io = InputOutput(yes=None, pretty=False, fancy_input=False)
        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True

        with patch("aider.io.sys.stdin", fake_stdin):
            with patch("builtins.input", side_effect=EOFError):
                result = io.confirm_ask("Run shell command?", default="y")

        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
