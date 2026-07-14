"""Tests for the branded Z login screen (presentation wrapper around auth)."""

from __future__ import annotations

import io as _io
import re
import unittest
from unittest.mock import MagicMock, patch

from rich.console import Console

from aider.z.login_screen import (
    LOGIN_OPTIONS,
    LOGIN_WORDMARK,
    LoginScreenState,
    TERMS_URL,
    _brand_block,
    compose_login_screen,
    prompt_login_choice,
    prompt_login_choice_plain,
    render_login_screen,
)


def _render_to_text(**kwargs) -> str:
    buf = _io.StringIO()
    # no_color=False so CI with NO_COLOR=1 still exercises truecolor styling
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=100,
        no_color=False,
        soft_wrap=False,
    )
    render_login_screen(console, **kwargs)
    return buf.getvalue()


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class LoginScreenRenderTest(unittest.TestCase):
    def test_wordmark_and_mascot_present(self):
        out = _render_to_text(version="v0.1.0")
        self.assertIn("██████████", out)  # big Z wordmark
        self.assertIn("(o-o", out)

    def test_wordmark_is_solid_blocks_not_box_drawing(self):
        joined = "\n".join(LOGIN_WORDMARK)
        for ch in "╔╗╚╝═":
            self.assertNotIn(ch, joined)
        for line in LOGIN_WORDMARK:
            self.assertEqual(len(line), len(LOGIN_WORDMARK[0]))

    def test_brand_block_does_not_wrap_at_common_widths(self):
        for width in (40, 60, 80, 100):
            buf = _io.StringIO()
            console = Console(
                file=buf,
                force_terminal=True,
                color_system="truecolor",
                width=width,
                no_color=False,
                soft_wrap=False,
            )
            console.print(_brand_block(unicode_ok=False))
            lines = _plain(buf.getvalue()).splitlines()
            face_lines = [ln for ln in lines if "(o" in ln or "o-" in ln]
            self.assertTrue(face_lines, msg=f"mascot missing at width={width}")
            for ln in face_lines:
                self.assertIn("(o-o", ln, msg=f"mascot wrapped at width={width}: {ln!r}")

    def test_hop_keeps_constant_canvas_height(self):
        heights = []
        for offset in (0, 1, 2):
            buf = _io.StringIO()
            console = Console(
                file=buf,
                force_terminal=True,
                color_system="truecolor",
                width=80,
                no_color=False,
                soft_wrap=False,
            )
            console.print(
                _brand_block(
                    mascot_offset=offset, unicode_ok=False, animate_canvas=True
                )
            )
            heights.append(len(_plain(buf.getvalue()).splitlines()))
        self.assertEqual(len(set(heights)), 1, heights)

    def test_resting_brand_has_no_hop_spacer(self):
        buf = _io.StringIO()
        console = Console(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=80,
            no_color=False,
            soft_wrap=False,
        )
        console.print(_brand_block(unicode_ok=False))
        lines = _plain(buf.getvalue()).splitlines()
        self.assertTrue(lines[0].strip().startswith("█"))

    def test_version_and_helper_lines(self):
        out = _render_to_text(version="v0.1.0")
        self.assertIn("Z CLI", out)
        self.assertIn("v0.1.0", out)
        self.assertIn("Get started", out)
        self.assertIn("How would you like to sign in?", out)
        self.assertIn("Enter", out)
        self.assertIn(TERMS_URL, out)

    def test_options_are_z_auth_methods_not_gemini(self):
        out = _render_to_text()
        self.assertIn("Continue with Google", out)
        self.assertIn("Continue with Email", out)
        self.assertIn("Continue with Phone", out)
        self.assertNotIn("Gemini", out)
        self.assertNotIn("Vertex", out)
        self.assertNotIn("API Key", out)

    def test_selected_option_marker_moves(self):
        out0 = _render_to_text(selected=0)
        out2 = _render_to_text(selected=2)
        self.assertIn("●", out0)
        self.assertIn("●", out2)
        i0 = out0.index("●")
        i2 = out2.index("●")
        self.assertNotEqual(i0, i2)

    def test_status_message_rendered(self):
        out = _render_to_text(status_message="Update available")
        self.assertIn("Update available", out)

    def test_orange_accent_used_not_green(self):
        out = _render_to_text()
        # Truecolor escape for #C96A2B → 201;106;43
        self.assertIn("201;106;43", out)
        self.assertNotIn("0;255;0", out)

    def test_option_order_google_email_phone(self):
        keys = [k for k, _ in LOGIN_OPTIONS]
        self.assertEqual(keys, ["google", "email", "phone"])

    def test_compose_group_contains_panel(self):
        group = compose_login_screen(LoginScreenState(selected=1, version="v1"))
        self.assertTrue(len(group.renderables) >= 3)


class PlainFallbackTest(unittest.TestCase):
    def _io_mock(self, answer: str):
        m = MagicMock()
        m.prompt_ask.return_value = answer
        m.pretty = False
        return m

    def test_plain_choice_mapping(self):
        io_mock = self._io_mock("2")
        self.assertEqual(prompt_login_choice_plain(io_mock), "email")
        io_mock = self._io_mock("1")
        self.assertEqual(prompt_login_choice_plain(io_mock), "google")
        io_mock = self._io_mock("3")
        self.assertEqual(prompt_login_choice_plain(io_mock), "phone")
        io_mock = self._io_mock("q")
        self.assertIsNone(prompt_login_choice_plain(io_mock))

    def test_prompt_login_choice_uses_plain_when_not_tty(self):
        io_mock = self._io_mock("1")
        io_mock.pretty = True
        with patch("sys.stdin") as fake_in, patch("sys.stdout") as fake_out:
            fake_in.isatty.return_value = False
            fake_out.isatty.return_value = False
            self.assertEqual(prompt_login_choice(io_mock), "google")
        io_mock.prompt_ask.assert_called_once()


class RunLoginFlowIntegrationTest(unittest.TestCase):
    def test_flow_routes_to_selected_provider(self):
        from aider.z import auth

        io_mock = MagicMock()
        io_mock.pretty = False
        io_mock.prompt_ask.return_value = "2"  # email

        fake_result = MagicMock()
        fake_result.ok = False
        fake_result.credentials = None
        fake_result.message = "nope"

        with patch.object(auth, "login_with_email", return_value=fake_result) as m_email:
            creds = auth.run_login_flow(io_mock)
        self.assertIsNone(creds)
        m_email.assert_called_once()

    def test_flow_cancel(self):
        from aider.z import auth

        io_mock = MagicMock()
        io_mock.pretty = False
        io_mock.prompt_ask.return_value = "q"
        creds = auth.run_login_flow(io_mock)
        self.assertIsNone(creds)


if __name__ == "__main__":
    unittest.main()
