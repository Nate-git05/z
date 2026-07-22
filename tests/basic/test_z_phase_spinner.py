"""Planning-phase mascot/eyes spinner — continuous status updates."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class PhaseSpinnerHelperTests(unittest.TestCase):
    def _coder(self):
        from aider.coders.base_coder import Coder
        from aider.z.turn_ux import TurnOrchestrator

        orch = TurnOrchestrator()
        coder = MagicMock(spec=Coder)
        coder.io = SimpleNamespace(
            z_theme=True,
            tool_output=MagicMock(),
            agent_busy=False,
            _stop_agent_busy=None,
            turn_orchestrator=orch,
            ensure_turn_ux=MagicMock(return_value=orch),
            start_busy_queue_reader=MagicMock(),
            stop_busy_queue_reader=MagicMock(),
        )
        coder.waiting_spinner = None
        coder.show_pretty = lambda: True
        # Bind real helpers
        coder._stop_waiting_spinner = Coder._stop_waiting_spinner.__get__(coder)
        coder._phase_spinner_start = Coder._phase_spinner_start.__get__(coder)
        coder._phase_spinner_update = Coder._phase_spinner_update.__get__(coder)
        coder._phase_spinner_stop = Coder._phase_spinner_stop.__get__(coder)
        coder._emit_retained_step = Coder._emit_retained_step.__get__(coder)
        return coder

    def test_kind_transition_restarts_and_retains_step(self):
        """matching skills (THINKING) -> exploring (EXPLORING) is a real
        phase-kind transition: the spinner restarts and a "✓ …" line is
        retained in scrollback for the phase that just finished."""
        from aider.z.mascot import MascotSpinner

        coder = self._coder()
        fake = MagicMock(spec=MascotSpinner)
        with patch("aider.coders.base_coder.waiting_display", return_value=fake):
            coder._phase_spinner_start("Planning — matching skills…")
            fake.start.assert_called_once()
            self.assertIs(coder.waiting_spinner, fake)

            coder._phase_spinner_update("Planning — exploring `bus` (1/3)…")
            self.assertEqual(fake.start.call_count, 2)
            fake.stop.assert_called_once()
            coder.io.tool_output.assert_any_call("✓ Thought it through")

            coder._phase_spinner_stop()
            self.assertIsNone(coder.waiting_spinner)
            coder.io.tool_output.assert_any_call("✓ Explored the codebase")

    def test_same_kind_update_does_not_restart(self):
        """Two PLANNING sub-labels in a row update the spinner text in
        place — no restart, no retained line."""
        from aider.z.mascot import MascotSpinner

        coder = self._coder()
        fake = MagicMock(spec=MascotSpinner)
        with patch("aider.coders.base_coder.waiting_display", return_value=fake):
            coder._phase_spinner_start("Planning — drafting approach checklist…")
            fake.start.assert_called_once()

            coder._phase_spinner_update("Planning — scoring blast radius…")
            fake.start.assert_called_once()
            fake.stop.assert_not_called()
            fake.set_text.assert_called()
            self.assertIn("Ctrl+C", fake.set_text.call_args[0][0])
            for call in coder.io.tool_output.call_args_list:
                self.assertNotIn("✓", call.args[0])

    def test_update_restarts_when_idle(self):
        from aider.z.mascot import MascotSpinner

        coder = self._coder()
        fake = MagicMock(spec=MascotSpinner)
        with patch("aider.coders.base_coder.waiting_display", return_value=fake):
            coder._phase_spinner_update("Planning — drafting approach…")
            fake.start.assert_called_once()
            self.assertIs(coder.waiting_spinner, fake)


class ExploreProgressCallbackTests(unittest.TestCase):
    def test_on_progress_receives_keyword_updates(self):
        from aider.z import explore as explore_mod

        seen: list[str] = []

        with patch.object(explore_mod, "_rg_available", return_value=True):
            with patch.object(
                explore_mod,
                "_search_rg",
                return_value=[("event_bus.cpp", "publish")],
            ):
                explore_mod._rank_candidates(
                    "implement event bus publish subscribe",
                    explore_mod.Path("."),
                    already_in_chat=[],
                    max_keywords=3,
                    max_files=4,
                    on_progress=seen.append,
                )
        self.assertTrue(seen)
        self.assertTrue(any("exploring" in s.lower() for s in seen), seen)


class MascotSetTextTests(unittest.TestCase):
    def test_set_text_updates_label(self):
        from aider.z.mascot import MascotSpinner

        sp = MascotSpinner("Planning — matching skills…")
        sp.set_text("Planning — exploring related files…")
        self.assertEqual(sp.text, "Planning — exploring related files…")


if __name__ == "__main__":
    unittest.main()
