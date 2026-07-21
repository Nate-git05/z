"""Escalation / confirm prompts stay short so terminal resize does not garble."""

from __future__ import annotations

import io
import os
import re
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_esc_")
os.environ["Z_HOME"] = _HOME

from rich.console import Console  # noqa: E402

from aider.io import InputOutput  # noqa: E402
from aider.z.escalation import render_escalation  # noqa: E402

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class EscalationResizeSafeTests(unittest.TestCase):
    def test_render_escalation_respects_console_width(self):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=60)
        long_q = (
            "Possible drift: recent reflection(s) touched CMakeLists.txt without "
            "resolving open checklist items that are quite long. Refocus?"
        )
        render_escalation(long_q, console=console, pretty=True)
        out = buf.getvalue()
        self.assertIn("Possible drift", out)
        self.assertIn("awaiting reply", out.lower())
        for line in out.splitlines():
            plain = _ANSI_RE.sub("", line)
            # Should roughly track console width=60 (emoji/borders may add a few cells)
            self.assertLessEqual(len(plain), 90, plain[:120])

    def test_confirm_ask_uses_short_cli_prompt_after_escalation(self):
        out = InputOutput(pretty=True, fancy_input=False, yes=None)
        out.pretty = True
        out.z_theme = True
        out.console = Console(file=io.StringIO(), force_terminal=True, width=80)
        out.tool_output = lambda *a, **k: None
        out.tool_error = lambda *a, **k: None
        out.append_chat_history = lambda *a, **k: None
        out.ring_bell = lambda *a, **k: None

        captured = {}

        class FakeSession:
            def prompt(self, message, **kwargs):
                captured["message"] = message
                return "n"

        out.prompt_session = FakeSession()

        long_q = (
            "Possible drift: recent reflection(s) touched CMakeLists.txt without "
            "resolving Implement a thread-safe event bus. Refocus on the original task instead?"
        )
        result = out.confirm_ask(long_q, default="n", explicit_yes_required=True)
        self.assertFalse(result)
        msg = captured.get("message", "")
        self.assertNotIn("Possible drift", msg)
        self.assertIn("(Y)es", msg)
        self.assertIn("(N)o", msg)
        self.assertLess(len(msg), 40)

    def test_confirm_ask_with_subject_also_short(self):
        out = InputOutput(pretty=True, fancy_input=False, yes=None)
        out.pretty = True
        out.z_theme = True
        out.console = Console(file=io.StringIO(), force_terminal=True, width=80)
        out.tool_output = lambda *a, **k: None
        out.append_chat_history = lambda *a, **k: None
        out.ring_bell = lambda *a, **k: None

        captured = {}

        class FakeSession:
            def prompt(self, message, **kwargs):
                captured["message"] = message
                return "y"

        out.prompt_session = FakeSession()
        ok = out.confirm_ask(
            "Refocus on the original task?",
            subject="Possible drift: touched CMakeLists.txt without resolving items.",
            default="n",
            explicit_yes_required=True,
        )
        self.assertTrue(ok)
        self.assertNotIn("Possible drift", captured["message"])
        self.assertLess(len(captured["message"]), 40)


if __name__ == "__main__":
    unittest.main()
