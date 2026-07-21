"""Tests for Z auth web app + SQLAlchemy models (SQLite stand-in for Postgres)."""

from __future__ import annotations

import os
import tempfile
import unittest

# Shared-cache memory DB so all connections see the same tables
_DB_PATH = tempfile.mktemp(suffix="_z_test.db")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
os.environ["Z_SECRET_KEY"] = "test-secret"
os.environ["Z_SERVER_DEV"] = "1"
os.environ["Z_PUBLIC_BASE_URL"] = "http://testserver"

from z_server.config import get_settings  # noqa: E402

get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from z_server.app import create_app  # noqa: E402
from z_server.db import init_db, reset_engine  # noqa: E402
from z_server.models import User, Workspace, WorkspaceMembership  # noqa: E402


class ZServerTestCase(unittest.TestCase):
    def setUp(self):
        reset_engine()
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
        # Drop and recreate for isolation
        if os.path.exists(_DB_PATH):
            os.unlink(_DB_PATH)
        init_db()
        self.app = create_app()
        # Avoid lifespan double-init issues; tables already created
        self.client = TestClient(self.app, raise_server_exceptions=True)

    def tearDown(self):
        reset_engine()
        get_settings.cache_clear()
        if os.path.exists(_DB_PATH):
            try:
                os.unlink(_DB_PATH)
            except OSError:
                pass

    def test_health(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_email_otp_creates_user_and_workspace(self):
        start = self.client.post(
            "/v1/auth/email/start",
            json={"email": "ada@example.com", "name": "Ada"},
        )
        self.assertEqual(start.status_code, 200, start.text)
        self.assertEqual(start.json()["method"], "otp")

        verify = self.client.post(
            "/v1/auth/email/verify",
            json={"email": "ada@example.com", "code": "000000", "name": "Ada"},
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        data = verify.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["user"]["email"], "ada@example.com")
        self.assertEqual(data["user"]["provider"], "email")
        self.assertEqual(data["workspace"]["name"], "Personal")
        self.assertEqual(data["workspace"]["role"], "owner")

        me = self.client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["name"], "Ada")

        # Refresh rotates tokens; old refresh no longer works.
        self.assertIn("refresh_token", data)
        refreshed = self.client.post(
            "/v1/auth/refresh",
            json={"refresh_token": data["refresh_token"]},
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        new = refreshed.json()
        self.assertIn("access_token", new)
        self.assertNotEqual(new["access_token"], data["access_token"])
        self.assertNotEqual(new["refresh_token"], data["refresh_token"])

        me2 = self.client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {new['access_token']}"},
        )
        self.assertEqual(me2.status_code, 200)

        stale = self.client.post(
            "/v1/auth/refresh",
            json={"refresh_token": data["refresh_token"]},
        )
        self.assertEqual(stale.status_code, 401)

    def test_phone_verify_dev(self):
        start = self.client.post("/v1/auth/phone/start", json={"phone": "+15551234567"})
        self.assertEqual(start.status_code, 200, start.text)

        verify = self.client.post(
            "/v1/auth/phone/verify",
            json={"phone": "+15551234567", "code": "000000"},
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        data = verify.json()
        self.assertEqual(data["user"]["phone"], "+15551234567")
        self.assertEqual(data["user"]["provider"], "phone")
        self.assertTrue(data["access_token"].startswith("z_"))

    def test_sqlalchemy_user_model_fields(self):
        from z_server.db import get_session_factory
        from z_server.services.tokens import find_or_create_user_by_email

        db = get_session_factory()()
        try:
            user = find_or_create_user_by_email(db, "x@y.com", "X")
            db.commit()
            self.assertIsInstance(user, User)
            self.assertEqual(user.email, "x@y.com")
            self.assertTrue(user.memberships)
            self.assertIsInstance(user.memberships[0].workspace, Workspace)
            self.assertIsInstance(user.memberships[0], WorkspaceMembership)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
