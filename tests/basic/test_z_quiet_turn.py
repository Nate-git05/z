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
    def test_default_working_and_waiting(self):
        from aider.z.ux_preamble import (
            DEFAULT_BUSY_LABEL,
            DEFAULT_WAITING_MODEL_LABEL,
            public_busy_label,
        )

        self.assertEqual(
            public_busy_label("Planning — building capability plan…"),
            DEFAULT_BUSY_LABEL,
        )
        self.assertEqual(
            public_busy_label("Waiting for gpt-4o", waiting_model=True),
            DEFAULT_WAITING_MODEL_LABEL,
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
    def test_preamble_format_still_available_for_escape(self):
        from aider.z.ux_preamble import TurnPreamble

        pre = TurnPreamble(verbose=False)
        pre.note_skills(["demo"])
        pre.note_explore(1)
        line = pre.format_lines()[0]
        self.assertIn("1 skill", line)
        self.assertIn("explore 1", line)

        outputs = []
        io = SimpleNamespace(tool_output=lambda *a, **k: outputs.append(a[0]))
        pre.flush(io)
        self.assertEqual(outputs, [])

    def test_phase_spinner_uses_working_label(self):
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

        fake = MagicMock(spec=MascotSpinner)
        with patch("aider.coders.base_coder.waiting_display", return_value=fake) as wd:
            with patch("sys.stdout.write"), patch("sys.stdout.flush"):
                coder._phase_spinner_start("Planning — building capability plan…")
        args = wd.call_args[0][0]
        self.assertIn("Working…", args)
        self.assertNotIn("capability plan", args)
        self.assertIn("Ctrl+C", args)
        coder._phase_spinner_stop()


if __name__ == "__main__":
    unittest.main()
