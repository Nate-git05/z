"""P1 terminal UX: mode chrome, usage opt-in, history filter."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import TestCase, mock
from unittest.mock import MagicMock

from aider.io import InputOutput
from aider.z.task_mode import TaskMode
from aider.z.ux_flags import history_mirror_status_enabled, show_usage_enabled
from aider.z.ux_prompt import format_mode_status_line, prompt_chevron, resolve_prompt_chrome


class TestUxPromptChrome(TestCase):
    def tearDown(self):
        for key in ("Z_PROMPT_ASCII", "Z_PROMPT_BRAND"):
            os.environ.pop(key, None)

    def test_default_is_chevron(self):
        chrome = resolve_prompt_chrome(
            edit_format="diff",
            default_edit_format="diff",
        )
        self.assertEqual(chrome, "› ")

    def test_plan_wins(self):
        chrome = resolve_prompt_chrome(
            forced_task_mode=TaskMode.PLAN,
            edit_format="ask",
            default_edit_format="diff",
        )
        self.assertTrue(chrome.startswith("PLAN›"))

    def test_ask_and_context(self):
        for fmt in ("ask", "context"):
            chrome = resolve_prompt_chrome(
                edit_format=fmt,
                default_edit_format="diff",
            )
            self.assertTrue(chrome.startswith("ASK›"), fmt)

    def test_help_and_other_format(self):
        self.assertTrue(
            resolve_prompt_chrome(edit_format="help", default_edit_format="diff").startswith(
                "help›"
            )
        )
        self.assertTrue(
            resolve_prompt_chrome(
                edit_format="architect", default_edit_format="diff"
            ).startswith("architect›")
        )

    def test_multiline(self):
        chrome = resolve_prompt_chrome(
            forced_task_mode=TaskMode.PLAN,
            edit_format="diff",
            default_edit_format="diff",
            multiline=True,
        )
        self.assertEqual(chrome, "PLAN multi› ")

    def test_ascii_escape(self):
        os.environ["Z_PROMPT_ASCII"] = "1"
        self.assertEqual(prompt_chevron(), ">")
        chrome = resolve_prompt_chrome(edit_format="diff", default_edit_format="diff")
        self.assertEqual(chrome, "> ")

    def test_brand_escape(self):
        os.environ["Z_PROMPT_BRAND"] = "1"
        chrome = resolve_prompt_chrome(edit_format="diff", default_edit_format="diff")
        self.assertEqual(chrome, "Z› ")

    def test_mode_status_lines(self):
        self.assertIn("PLAN", format_mode_status_line(mode="PLAN", plan_stage="clarify"))
        self.assertIn("clarify", format_mode_status_line(mode="PLAN", plan_stage="clarify"))
        self.assertIn("ASK", format_mode_status_line(mode="ASK"))
        self.assertIn("CODE", format_mode_status_line(mode="CODE"))


class TestShowUsage(TestCase):
    def tearDown(self):
        os.environ.pop("Z_SHOW_USAGE", None)

    def test_default_off(self):
        self.assertFalse(show_usage_enabled())
        self.assertFalse(show_usage_enabled(coder=MagicMock(show_cost=False, io=None)))

    def test_env_on(self):
        os.environ["Z_SHOW_USAGE"] = "1"
        self.assertTrue(show_usage_enabled())

    def test_coder_or_io_flag(self):
        self.assertTrue(show_usage_enabled(coder=MagicMock(show_cost=True, io=None)))
        io = MagicMock(show_cost=True)
        self.assertTrue(show_usage_enabled(io=io))
        self.assertTrue(show_usage_enabled(coder=MagicMock(show_cost=False, io=io)))


class TestShowUsageReport(TestCase):
    def tearDown(self):
        os.environ.pop("Z_SHOW_USAGE", None)

    def _coder_with_usage(self, *, show_cost=False):
        from aider.coders.base_coder import Coder

        coder = Coder.__new__(Coder)
        coder.usage_report = "Tokens: 1 sent, 2 received."
        coder.message_tokens_sent = 1
        coder.message_tokens_received = 2
        coder.message_cost = 0.0
        coder.total_cost = 0.0
        coder.total_tokens_sent = 0
        coder.total_tokens_received = 0
        coder.edit_format = "diff"
        coder.main_model = MagicMock(name="test-model")
        coder.show_cost = show_cost
        coder.io = MagicMock()
        coder.io.show_cost = False
        coder.event = MagicMock()
        return coder

    def test_default_does_not_print(self):
        coder = self._coder_with_usage()
        coder.show_usage_report()
        coder.io.tool_output.assert_not_called()
        coder.event.assert_called_once()
        self.assertEqual(coder.total_tokens_sent, 1)

    def test_show_cost_prints(self):
        coder = self._coder_with_usage(show_cost=True)
        coder.show_usage_report()
        coder.io.tool_output.assert_called_once_with(coder.usage_report)

    def test_env_prints(self):
        os.environ["Z_SHOW_USAGE"] = "1"
        coder = self._coder_with_usage()
        coder.show_usage_report()
        coder.io.tool_output.assert_called_once()


class TestHistoryMirror(TestCase):
    def tearDown(self):
        os.environ.pop("Z_UX_HISTORY_FULL", None)

    def test_flag_default_off(self):
        self.assertFalse(history_mirror_status_enabled())

    def test_tool_output_skips_history_by_default(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            hist = Path(td) / "chat.md"
            io = InputOutput(
                pretty=False,
                yes=True,
                fancy_input=False,
                input_history_file=None,
                chat_history_file=str(hist),
            )
            io.tool_output("Skills pulled: foo")
            text = hist.read_text(encoding="utf-8") if hist.exists() else ""
            self.assertNotIn("Skills pulled", text)

            io.tool_warning("Capability gaps: 2")
            text = hist.read_text(encoding="utf-8")
            self.assertIn("Capability gaps", text)

            io.session_note("Mode: PLAN — product edits blocked until /plan-exit")
            text = hist.read_text(encoding="utf-8")
            self.assertIn("Mode: PLAN", text)

    def test_history_full_mirrors_status(self):
        os.environ["Z_UX_HISTORY_FULL"] = "1"
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            hist = Path(td) / "chat.md"
            io = InputOutput(
                pretty=False,
                yes=True,
                fancy_input=False,
                input_history_file=None,
                chat_history_file=str(hist),
            )
            io.tool_output("Exploring related files")
            text = hist.read_text(encoding="utf-8")
            self.assertIn("Exploring related files", text)


class TestChatModeListsPlan(TestCase):
    def test_show_formats_includes_plan(self):
        from aider.commands import Commands

        io = MagicMock()
        # Capture tool_output lines from help listing
        lines = []
        io.tool_output = lambda *a, **k: lines.append(" ".join(str(x) for x in a))
        io.tool_error = io.tool_output
        coder = MagicMock()
        coder.main_model.edit_format = "diff"
        cmds = Commands(io, coder)
        cmds.cmd_chat_mode("")
        joined = "\n".join(lines)
        self.assertIn("plan", joined.lower())
        self.assertIn("plan interview", joined.lower())

    def test_enter_plan_mode_sets_forced(self):
        from aider.commands import Commands

        io = MagicMock()
        io.session_note = MagicMock()
        coder = MagicMock()
        coder.forced_task_mode = None
        coder.task_mode = None
        coder._inject_plan_mode_reminder = MagicMock()
        cmds = Commands(io, coder)
        with mock.patch("aider.z.plan_interview.plan_interview_enabled", return_value=False):
            cmds._enter_plan_mode()
        self.assertIs(coder.forced_task_mode, TaskMode.PLAN)
        io.session_note.assert_called_once()
        self.assertIn("PLAN", io.session_note.call_args[0][0])
