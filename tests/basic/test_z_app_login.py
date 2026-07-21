"""Tests for /app/login (CLI web sign-in page)."""

from __future__ import annotations

import os
import tempfile
import unittest

_DB_PATH = tempfile.mktemp(suffix="_z_app_login_test.db")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
os.environ["Z_SECRET_KEY"] = "test-secret-app-login"
os.environ["Z_SERVER_DEV"] = "1"
os.environ["Z_PUBLIC_BASE_URL"] = "http://testserver"
os.environ.pop("Z_FRONTEND_URL", None)

from z_server.config import get_settings  # noqa: E402

get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from z_server.app import create_app  # noqa: E402
from z_server.db import init_db, reset_engine  # noqa: E402


class AppLoginPageTest(unittest.TestCase):
    def setUp(self):
        reset_engine()
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
        os.environ.pop("Z_FRONTEND_URL", None)
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

    def test_app_login_renders_two_button_design(self):
        resp = self.client.get(
            "/app/login",
            params={
                "redirect_uri": "http://127.0.0.1:8765/callback",
                "state": "abc",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("Sign in to Z", body)
        self.assertIn("Continue with Google", body)
        self.assertIn("Continue with Z", body)
        self.assertIn("How would you like to sign in?", body)
        self.assertIn("/static/css/app_login.css", body)
        self.assertIn("/static/js/app_login.js", body)
        self.assertIn("redirect_uri", body)
        self.assertIn("data-state=\"abc\"", body)
        self.assertIn("/app/login/google/start", body)
        self.assertIn("Terms of Service", body)
        self.assertIn("Privacy Notice", body)

    def test_app_login_method_google_redirects_to_oauth_start(self):
        resp = self.client.get(
            "/app/login",
            params={
                "method": "google",
                "redirect_uri": "http://127.0.0.1:8765/callback",
                "state": "abc",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        loc = resp.headers.get("location", "")
        self.assertTrue(loc.startswith("/app/login/google/start"))
        self.assertIn("redirect_uri=", loc)
        self.assertIn("state=abc", loc)

    def test_app_login_method_z_opens_z_panel(self):
        resp = self.client.get(
            "/app/login",
            params={"method": "z", "state": "xyz"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("Sign in with Z", body)
        self.assertIn('data-method="z"', body)
        self.assertIn('id="z-panel"', body)
        # Choice buttons hidden when CLI already picked Z
        self.assertIn('id="auth-choice-buttons" hidden', body)

    def test_app_signup_page_copy(self):
        resp = self.client.get("/app/signup", params={"method": "z"})
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("Create your Z account", body)
        self.assertIn('data-intent="signup"', body)
        self.assertIn("/app/login", body)  # link to sign in

    def test_app_login_static_assets(self):
        css = self.client.get("/static/css/app_login.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("#0a0a0a", css.text.lower())
        self.assertIn("#c96a2b", css.text.lower())
        js = self.client.get("/static/js/app_login.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn("/v1/auth/email/start", js.text)
        self.assertIn("/v1/auth/phone/start", js.text)
        self.assertIn("notifyCli", js.text)
        self.assertIn("/v1/auth/cli/complete", js.text)

    def test_google_start_without_creds_shows_error_page(self):
        resp = self.client.get("/app/login/google/start", follow_redirects=False)
        # No Google client id configured → stay on login with error (200)
        # or redirect only if configured.
        self.assertIn(resp.status_code, (200, 302))
        if resp.status_code == 200:
            self.assertIn("Google", resp.text)


if __name__ == "__main__":
    unittest.main()
