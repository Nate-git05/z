"""P3 turn UX — orchestrator, queue, interrupt contract."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class TurnOrchestratorTests(unittest.TestCase):
    def test_transitions_idle_busy_waiting_busy_idle(self):
        from aider.z.turn_ux import TurnOrchestrator, TurnState

        orch = TurnOrchestrator()
        self.assertEqual(orch.state, TurnState.IDLE)

        orch.enter_busy("Planning — matching skills…")
        self.assertEqual(orch.state, TurnState.BUSY)
        self.assertIn("skills", orch.phase)

        orch.enter_waiting_input("plan_confirm")
        self.assertEqual(orch.state, TurnState.WAITING_INPUT)
        self.assertEqual(orch.waiting_kind, "plan_confirm")

        phase = orch.leave_waiting_input()
        self.assertEqual(orch.state, TurnState.BUSY)
        self.assertIn("skills", phase or "")

        orch.enter_idle()
        self.assertEqual(orch.state, TurnState.IDLE)
        self.assertIsNone(orch.phase)

    def test_enqueue_only_while_busy(self):
        from aider.z.turn_ux import TurnOrchestrator

        orch = TurnOrchestrator()
        self.assertFalse(orch.enqueue("next task"))
        orch.enter_busy("llm")
        self.assertTrue(orch.enqueue("next task"))
        self.assertTrue(orch.enqueue("another"))
        self.assertEqual(orch.queue_len, 2)

        orch.enter_waiting_input("confirm")
        self.assertFalse(orch.enqueue("should not queue"))
        self.assertEqual(orch.queue_len, 2)

        orch.leave_waiting_input()
        self.assertEqual(orch.pop_queued(), "next task")
        self.assertEqual(orch.pop_queued(), "another")
        self.assertIsNone(orch.pop_queued())

    def test_waiting_input_does_not_consume_queue(self):
        from aider.z.turn_ux import TurnOrchestrator

        orch = TurnOrchestrator()
        orch.enter_busy("planning")
        orch.enqueue("fix tests")
        orch.enter_waiting_input("confirm")
        # Confirm answers are separate — queue untouched
        self.assertEqual(orch.list_queue(), ["fix tests"])
        orch.leave_waiting_input()
        self.assertEqual(orch.pop_queued(), "fix tests")

    def test_ctrl_c_preserves_queue(self):
        from aider.z.turn_ux import TurnOrchestrator, TurnState

        orch = TurnOrchestrator()
        orch.enter_busy("planning")
        orch.enqueue("keep me")
        orch.interrupt_busy()
        self.assertEqual(orch.state, TurnState.IDLE)
        self.assertEqual(orch.queue_len, 1)
        self.assertEqual(orch.pop_queued(), "keep me")

    def test_status_label_includes_queue_and_interrupt(self):
        from aider.z.turn_ux import TurnOrchestrator

        orch = TurnOrchestrator()
        orch.enter_busy("Planning — building capability plan…")
        orch.enqueue("follow up")
        label = orch.status_label("Planning — building capability plan…")
        self.assertIn("Queued 1", label)
        self.assertIn("Ctrl+C", label)

    def test_queue_max_overflow(self):
        from aider.z.turn_ux import TurnOrchestrator

        orch = TurnOrchestrator(queue_max=2)
        orch.enter_busy("x")
        self.assertTrue(orch.enqueue("a"))
        self.assertTrue(orch.enqueue("b"))
        self.assertFalse(orch.enqueue("c"))
        self.assertEqual(orch.queue_len, 2)

    def test_feature_flag(self):
        from aider.z.turn_ux import turn_queue_enabled

        with patch.dict(os.environ, {"Z_TURN_QUEUE": "0"}):
            self.assertFalse(turn_queue_enabled(z_theme=True))
        with patch.dict(os.environ, {"Z_TURN_QUEUE": "1"}):
            self.assertTrue(turn_queue_enabled(z_theme=False))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("Z_TURN_QUEUE", None)
            self.assertTrue(turn_queue_enabled(z_theme=True))
            self.assertFalse(turn_queue_enabled(z_theme=False))


class QueueFifoDrainTests(unittest.TestCase):
    def test_coder_drains_queue_before_get_input(self):
        from aider.coders.base_coder import Coder
        from aider.z.turn_ux import TurnOrchestrator

        coder = MagicMock(spec=Coder)
        io = MagicMock()
        orch = TurnOrchestrator()
        orch.enter_busy("planning")
        orch.enqueue("first")
        orch.enqueue("second")
        orch.enter_idle()
        io.ensure_turn_ux = MagicMock(return_value=orch)
        io.pop_queued_user_message = orch.pop_queued
        io.tool_output = MagicMock()
        io.ring_bell = MagicMock()
        coder.io = io
        coder.get_input = MagicMock(return_value="should not call")

        coder._next_user_message = Coder._next_user_message.__get__(coder)
        msg1 = coder._next_user_message()
        self.assertEqual(msg1, "first")
        msg2 = coder._next_user_message()
        self.assertEqual(msg2, "second")
        # Empty → falls through to get_input
        msg3 = coder._next_user_message()
        self.assertEqual(msg3, "should not call")
        coder.get_input.assert_called_once()


class ClipboardBusyEnqueueTests(unittest.TestCase):
    def test_clipboard_busy_enqueues(self):
        from aider.copypaste import ClipboardWatcher
        from aider.z.turn_ux import TurnOrchestrator

        io = MagicMock()
        orch = TurnOrchestrator()
        orch.enter_busy("llm")
        io.turn_orchestrator = orch
        io.enqueue_user_message = MagicMock(side_effect=lambda t: orch.enqueue(t))
        io.interrupt_input = MagicMock()
        io.placeholder = None
        io.clipboard_watcher = None

        watcher = ClipboardWatcher(io)
        # Simulate one paste change without starting the thread
        watcher.last_clipboard = "old"
        with patch("aider.copypaste.pyperclip.paste", return_value="new paste"):
            # Run one iteration of the watch loop body
            current = "new paste"
            if current != watcher.last_clipboard:
                watcher.last_clipboard = current
                if orch.state.value == "busy":
                    io.enqueue_user_message(current)

        io.enqueue_user_message.assert_called_once_with("new paste")
        io.interrupt_input.assert_not_called()
        self.assertEqual(orch.queue_len, 1)


class IoWaitingInputScopeTests(unittest.TestCase):
    def test_confirm_freezes_queue_and_restores_busy(self):
        from aider.io import InputOutput
        from aider.z.turn_ux import TurnState

        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        orch = io.ensure_turn_ux()
        orch.enter_busy("planning")
        orch.enqueue("later")
        # --yes-always path: confirm_ask returns without real prompt
        ok = io.confirm_ask("Proceed?")
        self.assertTrue(ok)
        # After confirm, back to Busy with queue intact
        self.assertEqual(orch.state, TurnState.BUSY)
        self.assertEqual(orch.list_queue(), ["later"])


class NoSpinnerWithFullPromptTests(unittest.TestCase):
    def test_get_input_forces_idle(self):
        from aider.io import InputOutput
        from aider.z.turn_ux import TurnState

        io = InputOutput(pretty=False, fancy_input=False)
        orch = io.ensure_turn_ux()
        orch.enter_busy("planning")
        # Patch input path to avoid blocking
        with patch.object(io, "user_input"), patch("builtins.input", return_value="hi"):
            # No prompt_session → uses input()
            result = io.get_input("/tmp", [], [], MagicMock())
        self.assertEqual(result, "hi")
        self.assertEqual(orch.state, TurnState.IDLE)


class PhaseSpinnerHintTests(unittest.TestCase):
    def test_start_uses_orchestrator_label(self):
        from aider.coders.base_coder import Coder
        from aider.z.mascot import MascotSpinner

        coder = MagicMock(spec=Coder)
        io = SimpleNamespace(
            z_theme=True,
            tool_output=MagicMock(),
            agent_busy=False,
            _stop_agent_busy=None,
            turn_orchestrator=None,
            ensure_turn_ux=None,
            start_busy_queue_reader=MagicMock(),
            stop_busy_queue_reader=MagicMock(),
        )
        from aider.z.turn_ux import TurnOrchestrator

        orch = TurnOrchestrator()
        io.ensure_turn_ux = MagicMock(return_value=orch)
        io.turn_orchestrator = orch
        coder.io = io
        coder.waiting_spinner = None
        coder.show_pretty = lambda: True
        coder._stop_waiting_spinner = Coder._stop_waiting_spinner.__get__(coder)
        coder._phase_spinner_start = Coder._phase_spinner_start.__get__(coder)
        coder._phase_spinner_stop = Coder._phase_spinner_stop.__get__(coder)

        fake = MagicMock(spec=MascotSpinner)
        with patch("aider.coders.base_coder.waiting_display", return_value=fake) as wd:
            with patch("sys.stdout.write"), patch("sys.stdout.flush"):
                coder._phase_spinner_start("Planning — building capability plan…")
        args = wd.call_args[0][0]
        self.assertIn("Ctrl+C", args)
        self.assertEqual(orch.state.value, "busy")
        coder._phase_spinner_stop()


if __name__ == "__main__":
    unittest.main()
