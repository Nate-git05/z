"""Tests for Z account auth, credentials, and curated models."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aider.z.auth import (
    _dev_email_login,
    _dev_phone_login,
    _mint_dev_credentials,
    auth_dev_mode,
    whoami_text,
)
from aider.z.credentials import (
    Credentials,
    UserProfile,
    WorkspaceContext,
    clear_credentials,
    load_credentials,
    save_credentials,
)
from aider.z.models_catalog import ANTHROPIC_CURRENT, OPENAI_CURRENT, print_curated_models


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.creds_path = Path(self.tmp.name) / "credentials"
        self.addCleanup(self.tmp.cleanup)

    def test_save_and_load_roundtrip(self):
        creds = Credentials(
            access_token="tok_abc",
            refresh_token="ref_abc",
            user=UserProfile(email="a@b.com", name="Ada", provider="email"),
            workspace=WorkspaceContext(id="ws1", name="Personal", role="owner"),
            expires_at=9_999_999_999,
        )
        with patch("aider.z.credentials.CREDENTIALS_ENV_PATH", Path(self.tmp.name) / "c.env"):
            with patch("aider.z.credentials.ensure_z_home", return_value=Path(self.tmp.name)):
                save_credentials(creds, path=self.creds_path)
                loaded = load_credentials(path=self.creds_path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.access_token, "tok_abc")
        self.assertEqual(loaded.user.email, "a@b.com")
        self.assertEqual(loaded.workspace.name, "Personal")
        self.assertTrue(loaded.is_authenticated())

    def test_clear_credentials(self):
        creds = _mint_dev_credentials(provider="email", email="x@y.com", name="X")
        env_path = Path(self.tmp.name) / "c.env"
        with patch("aider.z.credentials.CREDENTIALS_ENV_PATH", env_path):
            with patch("aider.z.credentials.ensure_z_home", return_value=Path(self.tmp.name)):
                save_credentials(creds, path=self.creds_path)
                self.assertTrue(self.creds_path.exists())
                with patch("aider.z.credentials.CREDENTIALS_PATH", self.creds_path):
                    clear_credentials(path=self.creds_path)
                self.assertFalse(self.creds_path.exists())


class TestAuthFlows(unittest.TestCase):
    def test_dev_mode_default(self):
        os.environ.pop("Z_AUTH_DEV", None)
        os.environ.pop("Z_AUTH_URL", None)
        os.environ.pop("Z_GOOGLE_CLIENT_ID", None)
        self.assertTrue(auth_dev_mode())

    def test_dev_email_login(self):
        io = MagicMock()
        io.prompt_ask.side_effect = ["000000"]
        result = _dev_email_login(io, "ada@example.com", "Ada")
        self.assertTrue(result.ok)
        self.assertEqual(result.credentials.user.email, "ada@example.com")
        self.assertEqual(result.credentials.user.provider, "email")
        self.assertTrue(result.credentials.access_token.startswith("zdev_"))

    def test_dev_phone_login(self):
        io = MagicMock()
        io.prompt_ask.side_effect = ["000000"]
        result = _dev_phone_login(io, "+15551234567")
        self.assertTrue(result.ok)
        self.assertEqual(result.credentials.user.phone, "+15551234567")
        self.assertEqual(result.credentials.user.provider, "phone")

    def test_whoami_signed_out(self):
        with patch("aider.z.auth.current_session", return_value=None):
            text = whoami_text()
            self.assertIn("Not signed in", text)

    def test_whoami_signed_in(self):
        creds = _mint_dev_credentials(provider="google", email="g@ex.com", name="G")
        text = whoami_text(creds)
        self.assertIn("G", text)
        self.assertIn("g@ex.com", text)
        self.assertIn("API keys", text)


class TestModelsCatalog(unittest.TestCase):
    def test_anthropic_ids_from_docs(self):
        for mid in (
            "claude-fable-5",
            "claude-opus-4-8",
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
        ):
            self.assertIn(mid, ANTHROPIC_CURRENT)

    def test_openai_ids_from_docs(self):
        for mid in (
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.3-codex",
            "gpt-5.6",
        ):
            self.assertIn(mid, OPENAI_CURRENT)

    def test_aliases_resolve(self):
        from aider.models import MODEL_ALIASES

        self.assertEqual(MODEL_ALIASES["sonnet"], "claude-sonnet-5")
        self.assertEqual(MODEL_ALIASES["opus"], "claude-opus-4-8")
        self.assertEqual(MODEL_ALIASES["fable"], "claude-fable-5")
        self.assertEqual(MODEL_ALIASES["gpt-5.6"], "gpt-5.6-sol")
        self.assertEqual(MODEL_ALIASES["codex"], "gpt-5.3-codex")

    def test_openai_anthropic_lists_include_current(self):
        from aider.models import ANTHROPIC_MODELS, OPENAI_MODELS

        self.assertIn("claude-fable-5", ANTHROPIC_MODELS)
        self.assertIn("claude-opus-4-8", ANTHROPIC_MODELS)
        self.assertIn("claude-sonnet-5", ANTHROPIC_MODELS)
        self.assertIn("gpt-5.6-sol", OPENAI_MODELS)
        self.assertIn("gpt-5.3-codex", OPENAI_MODELS)

    def test_print_curated_models(self):
        io = MagicMock()
        print_curated_models(io)
        joined = " ".join(str(c.args[0]) for c in io.tool_output.call_args_list if c.args)
        self.assertIn("claude-fable-5", joined)
        self.assertIn("gpt-5.6-sol", joined)


class TestZCli(unittest.TestCase):
    def test_models_subcommand(self):
        from aider.z.cli import build_parser

        args = build_parser().parse_args(["models"])
        self.assertEqual(args.command, "models")
        args = build_parser().parse_args(["login", "--provider", "email"])
        self.assertEqual(args.command, "login")
        self.assertEqual(args.provider, "email")


if __name__ == "__main__":
    unittest.main()
