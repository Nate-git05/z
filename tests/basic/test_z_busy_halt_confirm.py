"""Busy chrome must not run over WaitingInput confirms."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class HaltBusyBeforeConfirmTests(unittest.TestCase):
    def test_ensure_prompt_ready_stops_spinner_and_enters_waiting(self):
        from aider.io import InputOutput
        from aider.z.turn_ux import TurnState

        io = InputOutput(pretty=False, fancy_input=False, z_theme=True)
        orch = io.ensure_turn_ux()
        orch.enter_busy("Planning — building capability plan…")

        stopper = MagicMock()
        io._stop_agent_busy = stopper
        io._busy_spinner_active = MagicMock(return_value=True)
        io.stop_busy_queue_reader = MagicMock()

        with patch("sys.stdout.write"), patch("sys.stdout.flush"):
            io._ensure_prompt_ready("confirm")

        stopper.assert_called()
        io.stop_busy_queue_reader.assert_called()
        self.assertEqual(orch.state, TurnState.WAITING_INPUT)
        self.assertFalse(io.agent_busy)

    def test_restore_does_not_restart_queue_without_live_spinner(self):
        from aider.io import InputOutput
        from aider.z.turn_ux import TurnState

        io = InputOutput(pretty=False, fancy_input=False, z_theme=True)
        orch = io.ensure_turn_ux()
        orch.enter_busy("Planning — building capability plan…")
        io._busy_spinner_active = MagicMock(return_value=False)
        io.start_busy_queue_reader = MagicMock()
        io.stop_busy_queue_reader = MagicMock()
        io._stop_agent_busy = MagicMock()

        with patch("sys.stdout.write"), patch("sys.stdout.flush"):
            io._ensure_prompt_ready("confirm")
        io._restore_after_prompt()

        self.assertEqual(orch.state, TurnState.BUSY)
        io.start_busy_queue_reader.assert_not_called()

    def test_confirm_ask_halts_before_panel(self):
        from aider.io import InputOutput

        io = InputOutput(pretty=True, fancy_input=False, z_theme=True, yes=True)
        orch = io.ensure_turn_ux()
        orch.enter_busy("Planning — building capability plan…")
        stopper = MagicMock()
        io._stop_agent_busy = stopper
        io._busy_spinner_active = MagicMock(return_value=True)
        io.stop_busy_queue_reader = MagicMock()

        with patch("sys.stdout.write"), patch("sys.stdout.flush"):
            with patch("aider.z.escalation.render_escalation"):
                ok = io.confirm_ask("Create new file?", subject="Cargo.toml")
        self.assertTrue(ok)
        stopper.assert_called()


class MascotStopClearsLineTests(unittest.TestCase):
    def test_stop_writes_clear_and_newline(self):
        from aider.z.mascot import MascotSpinner

        sp = MascotSpinner("Planning — building capability plan…")
        sp.is_tty = True
        sp.visible = True
        sp.last_display_len = 40
        writes = []

        with patch("aider.z.mascot.sys.stdout") as out:
            out.write.side_effect = lambda s: writes.append(s)
            out.flush = MagicMock()
            sp.console = MagicMock()
            sp.stop()

        blob = "".join(writes)
        self.assertIn("\n", blob)
        self.assertIn("\r", blob)


if __name__ == "__main__":
    unittest.main()
