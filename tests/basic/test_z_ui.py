"""Tests for the Z terminal UI theme, mascot, uncertainty views, and escalation."""

import io
import unittest
from unittest.mock import MagicMock, patch

from rich.console import Console

from aider.z.banner import render_startup_banner
from aider.z.escalation import render_escalation
from aider.z.mascot import (
    IDLE_MASCOT_ASCII,
    WORKING_FRAMES_ASCII,
    MascotSpinner,
    idle_mascot_lines,
    working_mascot_frame,
)
from aider.z.theme import ACCENT, TEXT, Z_COLORS, apply_z_palette
from aider.z.uncertainty_ui import (
    UncertaintyNote,
    UncertaintyStore,
    UncertaintyTier,
    render_note_detail,
    render_uncertainty_tree,
)


class TestZTheme(unittest.TestCase):
    def test_palette_has_single_accent(self):
        self.assertEqual(Z_COLORS["accent"], "#C96A2B")
        self.assertEqual(ACCENT, "#C96A2B")
        self.assertEqual(TEXT, "#F5F5F5")

    def test_apply_z_palette(self):
        args = MagicMock()
        apply_z_palette(args)
        self.assertEqual(args.user_input_color, "#F5F5F5")
        self.assertEqual(args.tool_warning_color, "#C96A2B")
        self.assertEqual(args.assistant_output_color, "#F5F5F5")
        self.assertEqual(args.code_theme, "monokai")
        self.assertEqual(args.completion_menu_current_bg_color, "#C96A2B")


class TestZMascot(unittest.TestCase):
    def test_idle_mascot_lines(self):
        lines = idle_mascot_lines(unicode_ok=False)
        self.assertEqual(lines, IDLE_MASCOT_ASCII)
        self.assertTrue(any("o o" in line or "o.o" in line for line in lines))
        self.assertTrue(any("[|" in line or "|[" in line or "[o" in line for line in lines))

    def test_working_frames_cycle(self):
        frames = [working_mascot_frame(i, unicode_ok=False) for i in range(8)]
        self.assertEqual(frames[:4], WORKING_FRAMES_ASCII)
        self.assertEqual(frames[4:], WORKING_FRAMES_ASCII)
        widths = {len(f) for f in WORKING_FRAMES_ASCII}
        self.assertEqual(len(widths), 1, "working frames must share a width")

    def test_mascot_spinner_start_stop(self):
        spinner = MascotSpinner("testing")
        spinner.is_tty = False  # avoid writing to real stdout
        spinner.start()
        spinner.stop()

    def test_waiting_spiral_fallback_without_tty(self):
        from aider.z.waiting_game import SpiralWaiting, waiting_display

        spiral = SpiralWaiting("waiting")
        spiral.is_tty = False
        spiral.fancy = False
        spiral.start()
        self.assertIsNotNone(spiral._fallback)
        spiral.stop()
        disp = waiting_display("x", interactive=False)
        self.assertIsInstance(disp, MascotSpinner)
        disp.is_tty = False
        disp.start()
        disp.stop()

    def test_spiral_soft_finish_api(self):
        from aider.z.waiting_game import SpiralWaiting

        spiral = SpiralWaiting("waiting")
        spiral.fancy = False  # use fallback path so we don't touch the TTY
        finished = []
        spiral.onEndComplete(lambda: finished.append(1))
        spiral.start()
        spiral.notifyFinish()
        # Fallback notifyFinish fires end immediately
        self.assertTrue(finished)
        spiral.stop()

    def test_spiral_frame_renders_and_rotates(self):
        from aider.z.waiting_game import render_spiral_frame, spiral_cells

        cells = spiral_cells(0.0, size=7)
        self.assertGreaterEqual(len(cells), 5)
        frame_a = render_spiral_frame(0.0, "working", color=False, unicode_ok=False)
        frame_b = render_spiral_frame(1.7, "working", color=False, unicode_ok=False)
        self.assertEqual(len(frame_a), 8)  # 7 rows + label
        self.assertTrue(any("@" in line or "*" in line or "." in line for line in frame_a[:7]))
        # Rotation should move at least one cell
        self.assertNotEqual("\n".join(frame_a[:7]), "\n".join(frame_b[:7]))
        # Thin arm — should not fill most of the canvas
        self.assertLess(len(cells), 22)

        from aider.z.waiting_game import AgentRunnerGame, SpiralWaiting

        self.assertIs(AgentRunnerGame, SpiralWaiting)


class TestZBanner(unittest.TestCase):
    def test_render_startup_banner_plain(self):
        with patch("builtins.print") as mock_print:
            render_startup_banner(
                version="0.1",
                model_line="Model: gpt-test",
                status_lines=["Git repo: none"],
                pretty=False,
            )
            printed = " ".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
            self.assertIn("Z 0.1", printed)
            self.assertIn("Model: gpt-test", printed)

    def test_render_startup_banner_pretty(self):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=80)
        render_startup_banner(
            console,
            version="0.86",
            model_line="Model: test-model with whole edit format",
            status_lines=["Git repo: none", "Repo-map: disabled"],
            pretty=True,
        )
        out = buf.getvalue()
        self.assertIn("Z", out)
        self.assertIn("0.86", out)
        self.assertIn("test-model", out)


class TestZUncertainty(unittest.TestCase):
    def setUp(self):
        self.store = UncertaintyStore()
        self.store.add(
            UncertaintyNote(
                id="u1",
                title="Risky change",
                tier=UncertaintyTier.HIGH_RISK,
                summary="Might break auth",
                files=["auth.py"],
                functions=["login"],
                suggested_fix="Add guard clause",
            )
        )
        self.store.add(
            UncertaintyNote(
                id="u2",
                title="Minor style",
                tier=UncertaintyTier.CONFIDENT,
                summary="Naming only",
            )
        )

    def test_store_get_by_id_and_index(self):
        self.assertEqual(self.store.get("u1").title, "Risky change")
        self.assertEqual(self.store.get("1").id, "u1")
        self.assertEqual(self.store.get("2").id, "u2")

    def test_mark_resolved(self):
        self.assertTrue(self.store.mark_resolved("u1"))
        self.assertEqual(len(self.store.active_notes()), 1)

    def test_render_tree_and_detail(self):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=80)
        render_uncertainty_tree(self.store, console=console, pretty=True)
        out = buf.getvalue()
        self.assertIn("Risky change", out)
        self.assertIn("high risk", out)

        buf2 = io.StringIO()
        console2 = Console(file=buf2, force_terminal=True, color_system="truecolor", width=80)
        render_note_detail(self.store.get("u1"), console=console2, pretty=True)
        detail = buf2.getvalue()
        self.assertIn("Might break auth", detail)
        self.assertIn("Suggested fix", detail)
        self.assertIn("fix", detail.lower())


class TestZEscalation(unittest.TestCase):
    def test_render_escalation_pretty(self):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=80)
        render_escalation(
            "Which approach should we take?",
            console=console,
            context="Two possible auth flows",
            options=["Refresh first", "Retry first"],
            pretty=True,
        )
        out = buf.getvalue()
        self.assertIn("needs your input", out.lower())
        self.assertIn("Which approach", out)
        self.assertIn("Refresh first", out)


class TestZAnnouncementsIntegration(unittest.TestCase):
    def test_get_announcements_says_z(self):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput
        from aider.models import Model

        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        io.z_theme = True
        model = Model("gpt-4o-mini")
        coder = Coder.create(main_model=model, io=io, edit_format="ask", fnames=[])
        lines = coder.get_announcements()
        self.assertTrue(lines[0].startswith("Z v"))


if __name__ == "__main__":
    unittest.main()
