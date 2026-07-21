"""Tests for the branded Z login screen (presentation wrapper around auth)."""

from __future__ import annotations

import io as _io
import os
import re
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
        out = _plain(_render_to_text(version="v0.1.0"))
        self.assertIn("##########", out)
        # Scientist mascot with glasses
        self.assertTrue(
            "[|o o|]" in out or "o o" in out,
            msg=f"scientist mascot missing: {out!r}",
        )

    def test_wordmark_is_pure_ascii(self):
        joined = "\n".join(LOGIN_WORDMARK)
        self.assertTrue(all(ord(ch) < 128 for ch in joined))
        for ch in "█╔╗╚╝═░▒":
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
            render_login_screen(console, version="v1")
            lines = _plain(buf.getvalue()).splitlines()
            face_lines = [
                ln
                for ln in lines
                if "o o" in ln or "o.o" in ln or "[|" in ln or "|o" in ln
            ]
            self.assertTrue(face_lines, msg=f"mascot missing at width={width}")
            for ln in face_lines:
                self.assertTrue(
                    "o" in ln,
                    msg=f"mascot wrapped at width={width}: {ln!r}",
                )
                # Line must fit terminal width (no wrap debris)
                self.assertLessEqual(len(ln), width, msg=f"line too long at {width}: {ln!r}")

    def test_no_unicode_box_drawing_in_menu(self):
        out = _plain(_render_to_text())
        for ch in "╭╮╰╯│─╔╗╚╝═":
            self.assertNotIn(ch, out)
        self.assertIn("+", out)
        self.assertIn("|", out)

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
        self.assertNotIn("OpenRouter", out)
        self.assertNotIn("API Key", out)

    def test_selected_option_marker_moves(self):
        out0 = _plain(_render_to_text(selected=0))
        out2 = _plain(_render_to_text(selected=2))
        self.assertIn("> 1.", out0)
        self.assertIn("> 3.", out2)
        self.assertNotIn("> 1.", out2)

    def test_status_message_rendered(self):
        out = _render_to_text(status_message="Update available")
        self.assertIn("Update available", out)

    def test_orange_accent_used_not_green(self):
        out = _render_to_text()
        self.assertIn("201;106;43", out)
        self.assertNotIn("0;255;0", out)

    def test_option_order_google_email_phone(self):
        keys = [k for k, _ in LOGIN_OPTIONS]
        self.assertEqual(keys, ["google", "email", "phone"])

    def test_compose_returns_text(self):
        text = compose_login_screen(LoginScreenState(selected=1, version="v1"))
        self.assertTrue(hasattr(text, "plain") or str(text))


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


class ZCliOnboardingGateTest(unittest.TestCase):
    def test_z_cli_skips_openrouter_login_offer(self):
        from aider.onboarding import select_default_model

        args = MagicMock()
        args.model = None
        io = MagicMock()
        analytics = MagicMock()

        with patch.dict(os.environ, {"Z_CLI": "1"}, clear=False):
            with patch("aider.onboarding.try_to_select_default_model", return_value=None):
                with patch("aider.onboarding.offer_openrouter_oauth") as oauth:
                    result = select_default_model(args, io, analytics)
        self.assertIsNone(result)
        oauth.assert_not_called()
        joined = " ".join(str(c.args[0]) for c in io.tool_output.call_args_list if c.args)
        self.assertIn("model", joined.lower())
        self.assertNotIn("OpenRouter", joined)


if __name__ == "__main__":
    unittest.main()
