"""Tests for browser-based BYOK/router setup (local POST callback)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import aider.z.auth as z_auth
from aider.z.auth import open_web_setup


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


def test_open_web_setup_falls_back_to_dev_flow_when_no_backend(monkeypatch):
    io = FakeIO()
    called = {}

    def fake_dev(io_arg, mode):
        called["mode"] = mode
        called["io"] = io_arg
        return {"model_id": "claude-sonnet-5", "env_var": "ANTHROPIC_API_KEY", "api_key": "sk"}

    with patch.object(z_auth, "auth_dev_mode", return_value=True), patch.object(
        z_auth, "_dev_web_setup", side_effect=fake_dev
    ) as dev_mock, patch.object(z_auth.webbrowser, "open") as browser_open:
        result = open_web_setup(io, "byok")

    assert result == {
        "model_id": "claude-sonnet-5",
        "env_var": "ANTHROPIC_API_KEY",
        "api_key": "sk",
    }
    assert called["mode"] == "byok"
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
            # Wait briefly for the local server to accept connections
            import time

            time.sleep(0.05)
            req = urllib.request.Request(
                redirect,
                data=json.dumps(
                    {
                        "state": "wrong-state-token",
                        "data": {
                            "model_id": "x",
                            "env_var": "K",
                            "api_key": "secret",
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


def test_open_web_setup_accepts_valid_post_callback():
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
                    "model_id": "claude-sonnet-5",
                    "env_var": "ANTHROPIC_API_KEY",
                    "api_key": "sk-live",
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
        result = open_web_setup(io, "byok")

    assert result == {
        "model_id": "claude-sonnet-5",
        "env_var": "ANTHROPIC_API_KEY",
        "api_key": "sk-live",
    }


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


def test_open_web_setup_router_dev_fallback():
    io = FakeIO()
    with patch.object(z_auth, "auth_dev_mode", return_value=True):
        result = open_web_setup(io, "router")
    assert result == {"workspace_id": "ws-dev", "plan": "dev"}


def test_open_web_login_falls_back_to_terminal_in_dev_mode():
    io = FakeIO()
    creds = MagicMock()
    with patch.object(z_auth, "auth_dev_mode", return_value=True), patch.object(
        z_auth, "run_login_flow", return_value=creds
    ) as login, patch.object(z_auth.webbrowser, "open") as browser_open:
        result = z_auth.open_web_login(io)
    assert result is creds
    login.assert_called_once_with(io, analytics=None)
    browser_open.assert_not_called()


def test_open_web_login_posts_credentials_via_callback():
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
                    "access_token": "tok-from-web",
                    "refresh_token": "ref",
                    "user": {
                        "email": "web@example.com",
                        "name": "Web User",
                        "provider": "google",
                    },
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
    ), patch.object(z_auth, "AUTH_TIMEOUT_SECONDS", 3), patch.object(
        z_auth.webbrowser, "open", side_effect=capture_open
    ), patch.object(z_auth, "save_credentials") as save, patch.object(
        z_auth, "apply_credentials_to_env"
    ):
        result = z_auth.open_web_login(io)

    assert result is not None
    assert result.access_token == "tok-from-web"
    assert result.user.email == "web@example.com"
    assert "auth.example.test/app/login" in opened["url"]
    save.assert_called_once()
    assert any("return to the terminal" in o.lower() or "close the browser" in o.lower() for o in io.outputs)
