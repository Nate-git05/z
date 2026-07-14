"""Tests for the branded Z login screen (presentation wrapper around auth)."""

from __future__ import annotations

import io as _io
import unittest
from unittest.mock import MagicMock, patch

from rich.console import Console

from aider.z.login_screen import (
    LOGIN_OPTIONS,
    LOGIN_WORDMARK,
    LoginScreenState,
    TERMS_URL,
    compose_login_screen,
    prompt_login_choice,
    prompt_login_choice_plain,
    render_login_screen,
)


def _render_to_text(**kwargs) -> str:
    buf = _io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=100)
    render_login_screen(console, **kwargs)
    return buf.getvalue()


class LoginScreenRenderTest(unittest.TestCase):
    def test_wordmark_and_mascot_present(self):
        out = _render_to_text(version="v0.1.0")
        self.assertIn("██████████", out)  # big Z wordmark
        # mascot body appears (unicode or ascii variant)
        self.assertTrue("(oᴗo" in out or "(o-o" in out)

    def test_version_and_helper_lines(self):
        out = _render_to_text(version="v0.1.0")
        self.assertIn("Z CLI", out)
        self.assertIn("v0.1.0", out)
        self.assertIn("Get started", out)
        self.assertIn("How would you like to sign in?", out)
        self.assertIn("(Use Enter to select)", out)
        self.assertIn(TERMS_URL, out)

    def test_options_are_z_auth_methods_not_gemini(self):
        out = _render_to_text()
        self.assertIn("Continue with Google", out)
        self.assertIn("Continue with Email", out)
        self.assertIn("Continue with Phone", out)
        # No Gemini carry-over
        self.assertNotIn("Gemini", out)
        self.assertNotIn("Vertex", out)
        self.assertNotIn("API Key", out)

    def test_selected_option_marker_moves(self):
        out0 = _render_to_text(selected=0)
        out2 = _render_to_text(selected=2)
        # Selected row uses the ● marker; different rows selected in each render
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
        # Gemini-style green should not appear as a styled color choice
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
