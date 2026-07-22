"""Tests for combined browser login+setup (local POST callback)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import aider.z.auth as z_auth
from aider.z.auth import open_web_setup
from aider.z.cli import _apply_web_setup_result, ensure_agent_session
from aider.z.credentials import Credentials, UserProfile, WorkspaceContext
from aider.z.onboarding import OnboardingConfig


class FakeIO:
    def __init__(self):
        self.outputs: list[str] = []
        self.errors: list[str] = []

    def tool_output(self, *a, **k):
        self.outputs.append(" ".join(str(x) for x in a))

    def tool_error(self, *a, **k):
        self.errors.append(" ".join(str(x) for x in a))

    def prompt_ask(self, prompt, default=None):
        return default or ""

    def confirm_ask(self, question, default="y", **kwargs):
        return str(default).strip().lower() in ("y", "yes", "true", "1")


def test_open_web_setup_falls_back_to_dev_flow_when_no_backend(monkeypatch):
    io = FakeIO()
    called = {}

    def fake_dev(io_arg, mode, *, skip_login=False):
        called["mode"] = mode
        called["skip_login"] = skip_login
        return {
            "credentials": None,
            "mode_result": {
                "model_id": "claude-sonnet-5",
                "env_var": "ANTHROPIC_API_KEY",
                "api_key": "sk",
            },
        }

    with patch.object(z_auth, "auth_dev_mode", return_value=True), patch.object(
        z_auth, "_dev_web_setup", side_effect=fake_dev
    ) as dev_mock, patch.object(z_auth.webbrowser, "open") as browser_open:
        result = open_web_setup(io, "byok", skip_login=True)

    assert result["mode_result"]["model_id"] == "claude-sonnet-5"
    assert called == {"mode": "byok", "skip_login": True}
    dev_mock.assert_called_once()
    browser_open.assert_not_called()


def test_open_web_setup_rejects_mismatched_state(monkeypatch):
    io = FakeIO()
    opened = {}

    def capture_open(url):
        opened["url"] = url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        redirect = qs["redirect_uri"][0]

        def post_wrong():
            import time

            time.sleep(0.05)
            req = urllib.request.Request(
                redirect,
                data=json.dumps(
                    {
                        "state": "wrong-state-token",
                        "data": {
                            "credentials": None,
                            "mode_result": {
                                "model_id": "x",
                                "env_var": "K",
                                "api_key": "secret",
                            },
                        },
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=2)
            except urllib.error.HTTPError:
                pass

        threading.Thread(target=post_wrong, daemon=True).start()
        return True

    with patch.object(z_auth, "auth_dev_mode", return_value=False), patch.object(
        z_auth, "get_auth_base_url", return_value="https://auth.example.test"
    ), patch.object(z_auth, "AUTH_TIMEOUT_SECONDS", 3), patch.object(
        z_auth.webbrowser, "open", side_effect=capture_open
    ):
        result = open_web_setup(io, "byok")

    assert result is None
    assert any("Invalid setup state" in e or "Setup failed" in e for e in io.errors)
    assert "auth.example.test/app/setup" in opened["url"]
    assert "skip_login=0" in opened["url"]


def test_open_web_setup_accepts_combined_post_callback():
    io = FakeIO()
    opened = {}

    def capture_open(url):
        opened["url"] = url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        redirect = qs["redirect_uri"][0]
        state = qs["state"][0]

        def post_ok():
            import time

            time.sleep(0.05)
            body = {
                "state": state,
                "data": {
                    "credentials": {
                        "access_token": "tok-web",
                        "refresh_token": "ref",
                        "user": {
                            "email": "web@example.com",
                            "name": "Web",
                            "provider": "google",
                        },
                        "workspace": {"id": "ws1", "name": "Personal", "role": "owner"},
                    },
                    "mode_result": {
                        "model_id": "claude-sonnet-5",
                        "env_var": "ANTHROPIC_API_KEY",
                        "api_key": "sk-live",
                    },
                },
            }
            req = urllib.request.Request(
                redirect,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)

        threading.Thread(target=post_ok, daemon=True).start()
        return True

    with patch.object(z_auth, "auth_dev_mode", return_value=False), patch.object(
        z_auth, "get_auth_base_url", return_value="https://auth.example.test"
    ), patch.object(z_auth, "AUTH_TIMEOUT_SECONDS", 3), patch.object(
        z_auth.webbrowser, "open", side_effect=capture_open
    ):
        result = open_web_setup(io, "byok", skip_login=False)

    assert result["credentials"]["access_token"] == "tok-web"
    assert result["mode_result"]["api_key"] == "sk-live"
    assert "skip_login=0" in opened["url"]


def test_open_web_setup_times_out_gracefully():
    io = FakeIO()

    with patch.object(z_auth, "auth_dev_mode", return_value=False), patch.object(
        z_auth, "get_auth_base_url", return_value="https://auth.example.test"
    ), patch.object(z_auth, "AUTH_TIMEOUT_SECONDS", 0.2), patch.object(
        z_auth.webbrowser, "open", return_value=True
    ):
        result = open_web_setup(io, "router")

    assert result is None
    assert any("Timed out" in e for e in io.errors)


def test_open_web_setup_router_dev_fallback_skip_login():
    io = FakeIO()
    with patch.object(z_auth, "auth_dev_mode", return_value=True), patch.object(
        z_auth, "run_login_flow"
    ) as login:
        result = open_web_setup(io, "router", skip_login=True)
    login.assert_not_called()
    assert result == {
        "credentials": None,
        "mode_result": {"workspace_id": "ws-dev", "plan": "dev"},
    }


def test_byok_setup_trip_uses_skip_login_true():
    io = FakeIO()
    creds = Credentials(
        access_token="tok",
        user=UserProfile(email="a@b.com", provider="email"),
        expires_at=9_999_999_999,
    )
    captured = {}

    with patch(
        "aider.z.auth.current_session", return_value=creds
    ), patch(
        "aider.z.auth.open_web_login"
    ) as login, patch(
        "aider.z.onboarding.load_config", return_value=OnboardingConfig()
    ), patch(
        "aider.z.login_screen.prompt_auth_mode_choice", return_value="byok"
    ), patch("aider.z.auth.prompt_byok_setup", return_value=True) as byok, patch(
        "aider.z.onboarding.save_auth_mode"
    ) as save_mode:
        ok = ensure_agent_session(io)

    assert ok
    login.assert_not_called()
    byok.assert_called_once()
    save_mode.assert_called_once_with("byok")


def test_login_happens_before_mode_choice_on_fresh_config():
    io = FakeIO()
    order: list[str] = []

    def fake_login(_io, **_k):
        order.append("login")
        return Credentials(
            access_token="tok",
            user=UserProfile(email="a@b.com", provider="email"),
            expires_at=9_999_999_999,
        )

    def fake_mode(_io, **_k):
        order.append("mode")
        return "byok"

    def fake_byok(_io):
        order.append("byok")
        return True

    with patch(
        "aider.z.auth.current_session", return_value=None
    ), patch(
        "aider.z.auth.open_web_login", side_effect=fake_login
    ), patch("aider.z.onboarding.load_config", return_value=OnboardingConfig()), patch(
        "aider.z.login_screen.prompt_auth_mode_choice", side_effect=fake_mode
    ), patch("aider.z.auth.prompt_byok_setup", side_effect=fake_byok), patch(
        "aider.z.onboarding.save_auth_mode"
    ):
        ok = ensure_agent_session(io)

    assert ok
    assert order == ["login", "mode", "byok"]


def test_router_mode_needs_no_second_web_trip():
    io = FakeIO()
    creds = Credentials(
        access_token="tok",
        user=UserProfile(email="a@b.com", provider="email"),
        expires_at=9_999_999_999,
    )
    with patch("aider.z.auth.current_session", return_value=creds), patch(
        "aider.z.auth.open_web_login"
    ) as login, patch(
        "aider.z.onboarding.load_config", return_value=OnboardingConfig()
    ), patch(
        "aider.z.login_screen.prompt_auth_mode_choice", return_value="router"
    ), patch(
        "aider.z.login_screen.prompt_router_model_choice",
        return_value="claude-sonnet-5",
    ), patch(
        "aider.z.cli._ensure_model_keys", return_value=True
    ), patch("aider.z.auth.open_web_setup") as setup, patch(
        "aider.z.onboarding.save_auth_mode"
    ) as save_mode, patch(
        "aider.z.onboarding.save_selected_model"
    ) as save_model:
        ok = ensure_agent_session(io)

    assert ok
    login.assert_not_called()
    setup.assert_not_called()
    save_mode.assert_called_once_with("router")
    save_model.assert_called_once_with("claude-sonnet-5")


def test_apply_web_setup_result_saves_credentials_when_present():
    result = {
        "credentials": {
            "access_token": "tok",
            "user": {"email": "a@b.com", "provider": "email"},
            "workspace": {"id": "ws1", "name": "Personal", "role": "owner"},
        },
        "mode_result": {
            "model_id": "claude-sonnet-5",
            "env_var": "ANTHROPIC_API_KEY",
            "api_key": "sk-test",
        },
    }
    with patch("aider.z.credentials.save_credentials") as save, patch(
        "aider.z.credentials.apply_credentials_to_env"
    ), patch("aider.z.onboarding.save_byok_key") as save_key, patch(
        "aider.z.onboarding.save_selected_model"
    ) as save_model:
        _apply_web_setup_result(result, mode="byok")

    save.assert_called_once()
    saved = save.call_args[0][0]
    assert isinstance(saved, Credentials)
    assert saved.access_token == "tok"
    save_key.assert_called_once_with("ANTHROPIC_API_KEY", "sk-test")
    save_model.assert_called_once_with("claude-sonnet-5")


def test_apply_web_setup_result_skips_credentials_when_absent():
    result = {
        "credentials": None,
        "mode_result": {"model_id": "claude-sonnet-5"},
    }
    with patch("aider.z.credentials.save_credentials") as save, patch(
        "aider.z.onboarding.save_selected_model"
    ) as save_model, patch("aider.z.onboarding.save_byok_key") as save_key:
        _apply_web_setup_result(result, mode="byok")

    save.assert_not_called()
    save_key.assert_not_called()
    save_model.assert_called_once_with("claude-sonnet-5")


def test_configured_mode_still_requires_login_when_signed_out():
    io = FakeIO()
    creds = Credentials(
        access_token="tok",
        user=UserProfile(email="a@b.com", provider="email"),
        expires_at=9_999_999_999,
    )
    with patch(
        "aider.z.onboarding.load_config",
        return_value=OnboardingConfig(
            auth_mode="byok", selected_model="claude-sonnet-5"
        ),
    ), patch("aider.z.auth.current_session", return_value=None), patch(
        "aider.z.auth.open_web_login", return_value=creds
    ) as login, patch(
        "aider.z.cli._model_missing_keys", return_value=[]
    ), patch("aider.z.auth.open_web_setup") as setup:
        ok = ensure_agent_session(io)
    assert ok
    login.assert_called_once()
    setup.assert_not_called()


def test_explicit_z_login_uses_web_login():
    from aider.z import cli as z_cli

    io = MagicMock()
    with patch("aider.z.auth.open_web_login", return_value=None) as login, patch(
        "aider.z.auth.open_web_setup"
    ) as setup, patch("aider.z.auth.run_login_flow") as terminal:
        code = z_cli.cmd_login(io)
    assert code == 1
    login.assert_called_once_with(io)
    setup.assert_not_called()
    terminal.assert_not_called()


def test_open_web_login_never_uses_cli_credential_entry_in_dev_mode():
    """Sign-in is web-only even when auth_dev_mode() would previously fall back."""
    from aider.z.auth import open_web_login

    io = FakeIO()
    with patch.object(z_auth, "auth_dev_mode", return_value=True), patch(
        "aider.z.login_screen.prompt_web_login_choice", return_value="z"
    ), patch.object(z_auth, "login_with_email") as email, patch.object(
        z_auth, "login_with_google"
    ) as google, patch.object(z_auth, "run_login_flow") as terminal, patch.object(
        z_auth, "_open_web_page_for_post_callback", return_value=None
    ) as web:
        result = open_web_login(io)

    assert result is None
    web.assert_called_once()
    assert web.call_args.kwargs.get("extra_params") == {
        "method": "z",
        "intent": "signin",
    }
    email.assert_not_called()
    google.assert_not_called()
    terminal.assert_not_called()


def test_open_web_login_asks_google_vs_z_then_opens_method_url():
    from aider.z.auth import open_web_login

    io = FakeIO()
    opened = {}

    def capture_open(url):
        opened["url"] = url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        redirect = qs["redirect_uri"][0]
        state = qs["state"][0]

        def post_ok():
            import time

            time.sleep(0.05)
            body = {
                "state": state,
                "data": {
                    "access_token": "tok-web",
                    "refresh_token": "ref",
                    "user": {"email": "a@b.com", "provider": "google"},
                    "workspace": {"id": "ws1", "name": "Personal", "role": "owner"},
                },
            }
            req = urllib.request.Request(
                redirect,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)

        threading.Thread(target=post_ok, daemon=True).start()
        return True

    with patch.object(z_auth, "auth_dev_mode", return_value=False), patch.object(
        z_auth, "get_auth_base_url", return_value="https://auth.example.test"
    ), patch.object(z_auth, "AUTH_TIMEOUT_SECONDS", 3), patch(
        "aider.z.login_screen.prompt_web_login_choice", return_value="google"
    ), patch(
        "aider.z.login_screen.prompt_auth_intent_choice"
    ) as intent_prompt, patch.object(
        z_auth.webbrowser, "open", side_effect=capture_open
    ), patch("aider.z.auth.save_credentials"), patch(
        "aider.z.auth.apply_credentials_to_env"
    ):
        creds = open_web_login(io)

    assert creds is not None
    intent_prompt.assert_not_called()
    assert "auth.example.test/app/login" in opened["url"]
    assert "method=google" in opened["url"]
    assert "intent=signin" in opened["url"]


def test_open_web_login_respects_explicit_signup_intent():
    from aider.z.auth import open_web_login

    io = FakeIO()
    opened = {}

    with patch.object(z_auth, "auth_dev_mode", return_value=False), patch.object(
        z_auth, "get_auth_base_url", return_value="https://auth.example.test"
    ), patch.object(z_auth, "AUTH_TIMEOUT_SECONDS", 0.2), patch(
        "aider.z.login_screen.prompt_web_login_choice", return_value="z"
    ), patch.object(
        z_auth.webbrowser,
        "open",
        side_effect=lambda url: opened.update(url=url) or True,
    ):
        open_web_login(io, intent="signup")

    assert "auth.example.test/app/signup" in opened["url"]
    assert "method=z" in opened["url"]
    assert "intent=signup" in opened["url"]


def test_auth_switch_uses_web_login_and_byok_skip_login():
    from aider.z import cli as z_cli

    io = MagicMock()
    creds = Credentials(
        access_token="tok",
        user=UserProfile(email="a@b.com", provider="email"),
        expires_at=9_999_999_999,
    )

    with patch(
        "aider.z.auth.current_session", return_value=None
    ), patch(
        "aider.z.auth.open_web_login", return_value=creds
    ) as login, patch(
        "aider.z.onboarding.clear_setup"
    ) as clear, patch(
        "aider.z.login_screen.prompt_auth_mode_choice", return_value="byok"
    ), patch("aider.z.auth.prompt_byok_setup", return_value=True) as byok, patch(
        "aider.z.onboarding.save_auth_mode"
    ) as save_mode, patch("aider.z.auth.run_login_flow") as terminal:
        code = z_cli.cmd_auth_switch(io)

    assert code == 0
    login.assert_called_once()
    clear.assert_called_once_with(clear_keys=True)
    terminal.assert_not_called()
    byok.assert_called_once()
    save_mode.assert_called_once_with("byok")
