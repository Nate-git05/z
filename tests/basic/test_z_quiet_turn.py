"""Quiet turn UX — one busy line, auto-create files, silent skills/explore."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import git

from aider.coders import Coder
from aider.models import Model
from aider.utils import GitTemporaryDirectory

_HOME = tempfile.mkdtemp(prefix="z_quiet_turn_")
os.environ["Z_HOME"] = _HOME
os.environ.pop("Z_UX_VERBOSE", None)
os.environ.pop("Z_UX_PREAMBLE", None)
os.environ.pop("Z_CONFIRM_NEW_FILES", None)


class PublicBusyLabelTests(unittest.TestCase):
    def test_default_shows_short_phase_kind_label(self):
        """Non-verbose default is a short live phase-kind label (not silent
        "Working…" for everything) — visible by default is the whole point
        of the live turn-phase indicator."""
        from aider.z.ux_preamble import public_busy_label

        self.assertEqual(
            public_busy_label("Planning — building capability plan…"),
            "Planning…",
        )
        self.assertEqual(
            public_busy_label("Waiting for gpt-4o", waiting_model=True),
            "Waiting for model…",
        )

    def test_verbose_keeps_detail(self):
        from aider.z.ux_preamble import public_busy_label

        with patch.dict(os.environ, {"Z_UX_VERBOSE": "1"}):
            self.assertEqual(
                public_busy_label("Planning — building capability plan…"),
                "Planning — building capability plan…",
            )
            self.assertEqual(
                public_busy_label("Waiting for gpt-4o", waiting_model=True),
                "Waiting for gpt-4o",
            )


class AutoCreateFilesTests(unittest.TestCase):
    def test_creates_multiple_new_files_without_confirm(self):
        with GitTemporaryDirectory():
            repo = git.Repo()
            Path("seed.txt").touch()
            repo.git.add("seed.txt")
            repo.git.commit("-m", "init")

            io = MagicMock()
            io.confirm_ask = MagicMock(return_value=False)
            io.yes = False
            coder = Coder.create(Model("gpt-3.5-turbo"), None, io, fnames=["seed.txt"])

            self.assertTrue(coder.allowed_to_edit("Cargo.toml"))
            self.assertTrue(coder.allowed_to_edit("src/lib.rs"))
            io.confirm_ask.assert_not_called()
            self.assertTrue(Path("Cargo.toml").exists())
            self.assertTrue(Path("src/lib.rs").exists())

    def test_confirm_escape_still_asks(self):
        from aider.z.ux_preamble import confirm_new_files_enabled

        with patch.dict(os.environ, {"Z_CONFIRM_NEW_FILES": "1"}):
            self.assertTrue(confirm_new_files_enabled())


class QuietSkillsExploreTests(unittest.TestCase):
    def test_empty_preamble_never_emits_dash_line(self):
        """Regression: hello → Planning · skills — · explore — · plan —"""
        from aider.z.ux_preamble import TurnPreamble

        pre = TurnPreamble(verbose=False)
        self.assertEqual(pre.format_lines(), [])
        outputs = []
        io = SimpleNamespace(tool_output=lambda *a, **k: outputs.append(a[0]))
        with patch.dict(os.environ, {"Z_UX_PREAMBLE": "1"}):
            pre.flush(io)
        self.assertEqual(outputs, [])
        self.assertNotIn(
            "Planning · skills — · explore — · plan —",
            "\n".join(outputs),
        )

    def test_preamble_format_still_available_for_escape(self):
        from aider.z.ux_preamble import TurnPreamble

        pre = TurnPreamble(verbose=False)
        pre.note_skills(["demo"])
        pre.note_explore(1)
        line = pre.format_lines()[0]
        self.assertIn("1 skill", line)
        self.assertIn("explore 1", line)
        self.assertNotIn("skills —", line)

        outputs = []
        io = SimpleNamespace(tool_output=lambda *a, **k: outputs.append(a[0]))
        pre.flush(io)
        self.assertEqual(outputs, [])

    def test_casual_hello_skips_planning_chrome(self):
        from aider.z.task_mode import looks_like_casual_chat

        self.assertTrue(looks_like_casual_chat("hello"))
        self.assertTrue(looks_like_casual_chat("hi"))
        self.assertFalse(looks_like_casual_chat("add a REST endpoint for users"))

    def test_phase_spinner_uses_short_phase_kind_label(self):
        """Non-verbose default shows the short "Planning…" kind label live
        (not the full detailed string, and not a silent "Working…")."""
        from aider.coders.base_coder import Coder
        from aider.z.mascot import MascotSpinner
        from aider.z.turn_ux import TurnOrchestrator

        coder = MagicMock(spec=Coder)
        io = MagicMock()
        orch = TurnOrchestrator()
        io.ensure_turn_ux = MagicMock(return_value=orch)
        io.turn_orchestrator = orch
        io.z_theme = True
        coder.io = io
        coder.waiting_spinner = None
        coder.verbose = False
        coder.show_pretty = lambda: True
        coder._stop_waiting_spinner = Coder._stop_waiting_spinner.__get__(coder)
        coder._phase_spinner_start = Coder._phase_spinner_start.__get__(coder)
        coder._phase_spinner_stop = Coder._phase_spinner_stop.__get__(coder)
        coder._emit_retained_step = Coder._emit_retained_step.__get__(coder)

        fake = MagicMock(spec=MascotSpinner)
        with patch("aider.coders.base_coder.waiting_display", return_value=fake) as wd:
            with patch("sys.stdout.write"), patch("sys.stdout.flush"):
                coder._phase_spinner_start("Planning — building capability plan…")
        args = wd.call_args[0][0]
        self.assertIn("Planning…", args)
        self.assertNotIn("capability plan", args)
        self.assertNotIn("Working…", args)
        self.assertIn("Ctrl+C", args)
        coder._phase_spinner_stop()


if __name__ == "__main__":
    unittest.main()
