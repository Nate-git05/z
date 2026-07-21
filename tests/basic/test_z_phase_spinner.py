"""Planning-phase mascot/eyes spinner — continuous status updates."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class PhaseSpinnerHelperTests(unittest.TestCase):
    def _coder(self):
        from aider.coders.base_coder import Coder

        coder = MagicMock(spec=Coder)
        coder.io = SimpleNamespace(z_theme=True, tool_output=MagicMock())
        coder.waiting_spinner = None
        coder.show_pretty = lambda: True
        # Bind real helpers
        coder._stop_waiting_spinner = Coder._stop_waiting_spinner.__get__(coder)
        coder._phase_spinner_start = Coder._phase_spinner_start.__get__(coder)
        coder._phase_spinner_update = Coder._phase_spinner_update.__get__(coder)
        coder._phase_spinner_stop = Coder._phase_spinner_stop.__get__(coder)
        return coder

    def test_start_update_stop_uses_mascot_spinner(self):
        from aider.z.mascot import MascotSpinner

        coder = self._coder()
        fake = MagicMock(spec=MascotSpinner)
        with patch("aider.coders.base_coder.waiting_display", return_value=fake):
            coder._phase_spinner_start("Planning — matching skills…")
            fake.start.assert_called_once()
            self.assertIs(coder.waiting_spinner, fake)

            coder._phase_spinner_update("Planning — exploring `bus` (1/3)…")
            fake.set_text.assert_called()
            self.assertIn("Ctrl+C", fake.set_text.call_args[0][0])

            coder._phase_spinner_stop()
            fake.stop.assert_called()
            self.assertIsNone(coder.waiting_spinner)

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
