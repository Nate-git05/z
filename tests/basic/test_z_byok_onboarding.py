"""Universal account login + orthogonal BYOK/router first-run choice."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_HOME = tempfile.mkdtemp(prefix="z_byok_onboard_")
os.environ["Z_HOME"] = _HOME

from aider.z.cli import (  # noqa: E402
    _has_explicit_model_flag,
    _start_agent,
    ensure_agent_session,
)
from aider.z.credentials import Credentials, UserProfile  # noqa: E402
from aider.z.onboarding import (  # noqa: E402
    OnboardingConfig,
    load_config,
    save_auth_mode,
    save_byok_key,
    save_selected_model,
)


def _creds() -> Credentials:
    return Credentials(
        access_token="tok",
        user=UserProfile(email="a@b.com", provider="email"),
        expires_at=9_999_999_999,
    )


class FakeIO:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.outputs: list[str] = []
        self.errors: list[str] = []
        self.pretty = False

    def tool_output(self, *a, **k):
        self.outputs.append(" ".join(str(x) for x in a))

    def tool_error(self, *a, **k):
        self.errors.append(" ".join(str(x) for x in a))

    def prompt_ask(self, prompt, default=None):
        if self.answers:
            return self.answers.pop(0)
        return default or ""


class ConfigMergeTest(unittest.TestCase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="z_byok_cfg_"))
        os.environ["Z_HOME"] = str(self._dir)
        self._prev_anthropic = os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if self._prev_anthropic is not None:
            os.environ["ANTHROPIC_API_KEY"] = self._prev_anthropic

    def test_config_json_merges_auth_mode_and_selected_model_independently(self):
        save_auth_mode("byok")
        save_selected_model("claude-sonnet-5")
        cfg = load_config()
        self.assertEqual(cfg.auth_mode, "byok")
        self.assertEqual(cfg.selected_model, "claude-sonnet-5")
        # Calling save_auth_mode again must not erase selected_model.
        save_auth_mode("router")
        cfg2 = load_config()
        self.assertEqual(cfg2.auth_mode, "router")
        self.assertEqual(cfg2.selected_model, "claude-sonnet-5")

    def test_byok_key_saved_to_separate_file_not_credentials_env(self):
        save_byok_key("ANTHROPIC_API_KEY", "sk-test-key")
        byok_env = self._dir / "byok.env"
        creds_env = self._dir / "credentials.env"
        self.assertTrue(byok_env.exists())
        self.assertIn("ANTHROPIC_API_KEY=sk-test-key", byok_env.read_text())
        self.assertFalse(creds_env.exists())
        self.assertEqual(os.environ.get("ANTHROPIC_API_KEY"), "sk-test-key")


class EnsureSessionOrderTest(unittest.TestCase):
    def test_login_happens_before_mode_choice_on_fresh_config(self):
        """Login first; then mode choice; BYOK setup with skip_login=True."""
        io = FakeIO()
        order: list[str] = []

        def fake_login(_io, **_k):
            order.append("login")
            return _creds()

        def fake_mode(_io, **_k):
            order.append("mode")
            return "byok"

        def fake_byok(_io):
            order.append("byok")
            return True

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("Z_SKIP_ACCOUNT", None)
            with patch("aider.z.auth.current_session", return_value=None):
                with patch("aider.z.auth.open_web_login", side_effect=fake_login):
                    with patch(
                        "aider.z.onboarding.load_config",
                        return_value=OnboardingConfig(),
                    ):
                        with patch(
                            "aider.z.login_screen.prompt_auth_mode_choice",
                            side_effect=fake_mode,
                        ):
                            with patch(
                                "aider.z.auth.prompt_byok_setup",
                                side_effect=fake_byok,
                            ):
                                with patch("aider.z.onboarding.save_auth_mode"):
                                    ok = ensure_agent_session(io)
        self.assertTrue(ok)
        self.assertEqual(order, ["login", "mode", "byok"])


class ByokSetupTest(unittest.TestCase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="z_byok_setup_"))
        os.environ["Z_HOME"] = str(self._dir)
        self._prev_some = os.environ.pop("SOME_KEY", None)

    def tearDown(self):
        os.environ.pop("SOME_KEY", None)
        if self._prev_some is not None:
            os.environ["SOME_KEY"] = self._prev_some

    def test_byok_setup_only_prompts_for_actually_missing_keys(self):
        from aider.z.auth import prompt_byok_setup

        io = FakeIO(answers=["1", "1", "sk-from-prompt"])
        fake_model = MagicMock()
        fake_model.missing_keys = ["SOME_KEY"]
        fake_model.keys_in_environment = False

        with patch(
            "aider.models.fuzzy_match_models",
            return_value=["claude-sonnet-5"],
        ):
            with patch("aider.models.Model", return_value=fake_model) as ModelCls:
                with patch(
                    "aider.z.models_catalog.CURATED_SECTIONS",
                    [("Anthropic (Claude)", ["claude-sonnet-5"])],
                ):
                    ok = prompt_byok_setup(io)

        self.assertTrue(ok)
        ModelCls.assert_called_once_with("claude-sonnet-5")
        # Only the model's missing_keys entry was requested — no hardcoded map.
        self.assertEqual(os.environ.get("SOME_KEY"), "sk-from-prompt")
        self.assertEqual(load_config().selected_model, "claude-sonnet-5")
        self.assertFalse(io.answers)  # family + model + key all consumed

    def test_byok_setup_rejects_unrecognized_model_name(self):
        from aider.z.auth import prompt_byok_setup

        io = FakeIO(answers=["99", "totally-fake-nonexistent-model-xyz123"])
        with patch("aider.models.fuzzy_match_models", return_value=[]):
            with patch("aider.models.Model") as ModelCls:
                with patch(
                    "aider.z.models_catalog.CURATED_SECTIONS",
                    [("Anthropic (Claude)", ["claude-sonnet-5"])],
                ):
                    ok = prompt_byok_setup(io)
        self.assertFalse(ok)
        ModelCls.assert_not_called()
        self.assertTrue(any("not a recognized model" in e for e in io.errors))

    def test_byok_setup_suggests_close_matches_for_typo(self):
        from aider.z.auth import prompt_byok_setup

        io = FakeIO(answers=["99", "claude-sonet-5"])
        with patch(
            "aider.models.fuzzy_match_models",
            return_value=["claude-sonnet-4-5", "claude-sonnet-4-6", "claude-sonnet-5"],
        ):
            with patch("aider.models.Model") as ModelCls:
                with patch(
                    "aider.z.models_catalog.CURATED_SECTIONS",
                    [("Anthropic (Claude)", ["claude-sonnet-5"])],
                ):
                    ok = prompt_byok_setup(io)
        self.assertFalse(ok)
        ModelCls.assert_not_called()
        joined = " ".join(io.errors)
        self.assertIn("Did you mean", joined)
        self.assertIn("claude-sonnet-5", joined)


class StartAgentInjectTest(unittest.TestCase):
    def setUp(self):
        self._prev_z_cli = os.environ.get("Z_CLI")

    def tearDown(self):
        if self._prev_z_cli is None:
            os.environ.pop("Z_CLI", None)
        else:
            os.environ["Z_CLI"] = self._prev_z_cli

    def test_has_explicit_model_flag(self):
        self.assertTrue(_has_explicit_model_flag(["--model", "sonnet"]))
        self.assertTrue(_has_explicit_model_flag(["--model=sonnet"]))
        self.assertFalse(_has_explicit_model_flag([]))
        self.assertFalse(_has_explicit_model_flag(["--yes"]))

    def test_start_agent_injects_saved_model_when_none_specified(self):
        captured = {}

        def fake_main(*, argv=None):
            captured["argv"] = list(argv or [])
            return 0

        with patch("aider.z.cli.ensure_agent_session", return_value=True):
            with patch(
                "aider.z.onboarding.load_config",
                return_value=OnboardingConfig(
                    auth_mode="byok", selected_model="claude-haiku-4-5"
                ),
            ):
                with patch("aider.main.main", side_effect=fake_main):
                    code = _start_agent([])
        self.assertEqual(code, 0)
        self.assertEqual(captured["argv"], ["--model", "claude-haiku-4-5"])

    def test_start_agent_respects_explicit_model_flag_over_saved_choice(self):
        captured = {}

        def fake_main(*, argv=None):
            captured["argv"] = list(argv or [])
            return 0

        with patch("aider.z.cli.ensure_agent_session", return_value=True):
            with patch(
                "aider.z.onboarding.load_config",
                return_value=OnboardingConfig(
                    auth_mode="byok", selected_model="claude-haiku-4-5"
                ),
            ):
                with patch("aider.main.main", side_effect=fake_main):
                    code = _start_agent(["--model", "gpt-5.6"])
        self.assertEqual(code, 0)
        self.assertEqual(captured["argv"], ["--model", "gpt-5.6"])


class AuthModeChoiceTest(unittest.TestCase):
    def test_plain_auth_mode_choice(self):
        from aider.z.login_screen import prompt_auth_mode_choice_plain

        io = FakeIO(answers=["1"])
        self.assertEqual(prompt_auth_mode_choice_plain(io), "byok")
        io = FakeIO(answers=["2"])
        self.assertEqual(prompt_auth_mode_choice_plain(io), "router")
        io = FakeIO(answers=["q"])
        self.assertIsNone(prompt_auth_mode_choice_plain(io))

    def test_mode_labels_are_post_auth_not_login(self):
        from aider.z.login_screen import AUTH_MODE_OPTIONS

        labels = " ".join(label for _k, label in AUTH_MODE_OPTIONS).lower()
        self.assertIn("api key", labels)
        self.assertIn("router", labels)
        self.assertNotIn("sign up", labels)
        self.assertNotIn("sign in", labels)

    def test_router_model_choice_plain(self):
        from aider.z.login_screen import prompt_router_model_choice_plain, router_model_options

        opts = router_model_options()
        self.assertGreaterEqual(len(opts), 2)
        io = FakeIO(answers=["1"])
        self.assertEqual(prompt_router_model_choice_plain(io), opts[0][0])


class RememberedModeTest(unittest.TestCase):
    def test_signed_in_with_saved_byok_skips_all_prompts(self):
        io = FakeIO()
        with patch("aider.z.auth.current_session", return_value=_creds()), patch(
            "aider.z.auth.open_web_login"
        ) as login, patch(
            "aider.z.onboarding.load_config",
            return_value=OnboardingConfig(
                auth_mode="byok", selected_model="claude-sonnet-5"
            ),
        ), patch(
            "aider.z.login_screen.prompt_auth_mode_choice"
        ) as mode, patch(
            "aider.z.auth.prompt_byok_setup"
        ) as byok, patch(
            "aider.z.login_screen.prompt_router_model_choice"
        ) as router_model:
            ok = ensure_agent_session(io)
        self.assertTrue(ok)
        login.assert_not_called()
        mode.assert_not_called()
        byok.assert_not_called()
        router_model.assert_not_called()


class RouterModeFlowTest(unittest.TestCase):
    def test_router_mode_asks_for_preferred_model_after_login(self):
        io = FakeIO()
        order: list[str] = []

        def fake_login(_io, **_k):
            order.append("login")
            return _creds()

        def fake_mode(_io, **_k):
            order.append("mode")
            return "router"

        def fake_router_model(_io, **_k):
            order.append("router_model")
            return "claude-sonnet-5"

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("Z_SKIP_ACCOUNT", None)
            with patch("aider.z.auth.current_session", return_value=None):
                with patch("aider.z.auth.open_web_login", side_effect=fake_login):
                    with patch(
                        "aider.z.onboarding.load_config",
                        return_value=OnboardingConfig(),
                    ):
                        with patch(
                            "aider.z.login_screen.prompt_auth_mode_choice",
                            side_effect=fake_mode,
                        ):
                            with patch(
                                "aider.z.login_screen.prompt_router_model_choice",
                                side_effect=fake_router_model,
                            ):
                                with patch(
                                    "aider.z.onboarding.save_auth_mode"
                                ) as save_mode:
                                    with patch(
                                        "aider.z.onboarding.save_selected_model"
                                    ) as save_model:
                                        ok = ensure_agent_session(io)

        self.assertTrue(ok)
        self.assertEqual(order, ["login", "mode", "router_model"])
        save_mode.assert_called_with("router")
        save_model.assert_called_with("claude-sonnet-5")

    def test_start_agent_injects_router_selected_model(self):
        captured = {}

        def fake_main(argv=None, **_k):
            captured["argv"] = list(argv or [])
            return 0

        with patch("aider.z.cli.ensure_agent_session", return_value=True):
            with patch(
                "aider.z.onboarding.load_config",
                return_value=OnboardingConfig(
                    auth_mode="router", selected_model="claude-haiku-4-5"
                ),
            ):
                with patch("aider.main.main", side_effect=fake_main):
                    code = _start_agent([])
        self.assertEqual(code, 0)
        self.assertEqual(captured["argv"], ["--model", "claude-haiku-4-5"])


class CuratedSectionsTest(unittest.TestCase):
    def test_includes_extra_providers(self):
        from aider.z.models_catalog import CURATED_SECTIONS

        titles = [t for t, _ in CURATED_SECTIONS]
        for expected in (
            "Anthropic (Claude)",
            "OpenAI",
            "DeepSeek",
            "Groq",
            "Gemini",
            "Kimi / Moonshot",
        ):
            self.assertIn(expected, titles)


if __name__ == "__main__":
    unittest.main()
