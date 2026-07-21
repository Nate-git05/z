"""CLI auth bridge — browser complete + CLI poll when localhost is blocked."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

_DB_PATH = tempfile.mktemp(suffix="_z_cli_bridge_test.db")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
os.environ["Z_SECRET_KEY"] = "test-secret-cli-bridge"
os.environ["Z_SERVER_DEV"] = "1"
os.environ["Z_PUBLIC_BASE_URL"] = "http://testserver"
os.environ.pop("Z_FRONTEND_URL", None)

from z_server.config import get_settings  # noqa: E402

get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from aider.z import auth as z_auth  # noqa: E402
from z_server.app import create_app  # noqa: E402
from z_server.db import init_db, reset_engine  # noqa: E402


class CliAuthBridgeApiTest(unittest.TestCase):
    def setUp(self):
        reset_engine()
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
        if os.path.exists(_DB_PATH):
            os.unlink(_DB_PATH)
        init_db()
        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self):
        reset_engine()
        get_settings.cache_clear()
        if os.path.exists(_DB_PATH):
            try:
                os.unlink(_DB_PATH)
            except OSError:
                pass

    def test_complete_then_poll_returns_session_once(self):
        state = "bridge-state-abc12345"
        session = {
            "access_token": "z_tok",
            "refresh_token": "zref",
            "user": {"email": "a@b.com", "provider": "email"},
            "workspace": {"id": "ws1", "name": "Personal", "role": "owner"},
        }
        pending = self.client.get("/v1/auth/cli/poll", params={"state": state})
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["status"], "pending")

        done = self.client.post(
            "/v1/auth/cli/complete",
            json={"state": state, "data": session},
        )
        self.assertEqual(done.status_code, 200, done.text)
        self.assertTrue(done.json()["ok"])

        ready = self.client.get("/v1/auth/cli/poll", params={"state": state})
        self.assertEqual(ready.status_code, 200)
        body = ready.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["data"]["access_token"], "z_tok")

        again = self.client.get("/v1/auth/cli/poll", params={"state": state})
        self.assertEqual(again.json()["status"], "consumed")

    def test_complete_requires_access_token(self):
        resp = self.client.post(
            "/v1/auth/cli/complete",
            json={"state": "bridge-state-xyz98765", "data": {"user": {}}},
        )
        self.assertEqual(resp.status_code, 400)


class FakeIO:
    def tool_output(self, *a, **k):
        pass

    def tool_error(self, *a, **k):
        pass

    def tool_warning(self, *a, **k):
        pass


def test_open_web_login_succeeds_via_cli_poll_when_localhost_unused():
    """Simulate browser posting only to the server bridge (no localhost POST)."""
    from aider.z.auth import open_web_login

    io = FakeIO()
    opened = {}
    state_box = {}

    def capture_open(url):
        opened["url"] = url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        state_box["state"] = qs["state"][0]
        return True

    # Serve poll/complete via the real FastAPI app on a background TestClient
    # by patching requests.get used by the CLI poller.
    reset_engine()
    get_settings.cache_clear()
    if os.path.exists(_DB_PATH):
        os.unlink(_DB_PATH)
    init_db()
    app = create_app()
    client = TestClient(app)

    session = {
        "access_token": "tok-poll",
        "refresh_token": "ref-poll",
        "user": {"email": "poll@example.com", "provider": "email"},
        "workspace": {"id": "ws", "name": "Personal", "role": "owner"},
    }

    def fake_get(url, params=None, timeout=10):
        state = (params or {}).get("state")
        if state and not state_box.get("posted"):
            client.post(
                "/v1/auth/cli/complete",
                json={"state": state, "data": session},
            )
            state_box["posted"] = True
        resp = client.get("/v1/auth/cli/poll", params={"state": state})
        mock = MagicMock()
        mock.status_code = resp.status_code
        mock.json.return_value = resp.json()
        return mock

    with patch.object(z_auth, "auth_dev_mode", return_value=False), patch.object(
        z_auth, "get_auth_base_url", return_value="https://auth.example.test"
    ), patch.object(z_auth, "AUTH_TIMEOUT_SECONDS", 5), patch(
        "aider.z.login_screen.prompt_web_login_choice", return_value="z"
    ), patch.object(z_auth.webbrowser, "open", side_effect=capture_open), patch.object(
        z_auth.requests, "get", side_effect=fake_get
    ), patch("aider.z.auth.save_credentials"), patch(
        "aider.z.auth.apply_credentials_to_env"
    ):
        creds = open_web_login(io)

    assert creds is not None
    assert creds.access_token == "tok-poll"
    assert "app/login" in opened["url"]
    assert "method=z" in opened["url"]


if __name__ == "__main__":
    unittest.main()
