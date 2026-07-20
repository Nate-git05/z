"""Tests for waitlist API and landing page."""

from __future__ import annotations

import os
import tempfile
import unittest

_DB_PATH = tempfile.mktemp(suffix="_z_waitlist_test.db")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
os.environ["Z_SECRET_KEY"] = "test-secret-waitlist"
os.environ["Z_SERVER_DEV"] = "1"
os.environ["Z_PUBLIC_BASE_URL"] = "http://testserver"

from z_server.config import get_settings  # noqa: E402

get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from z_server.app import create_app  # noqa: E402
from z_server.db import init_db, reset_engine  # noqa: E402
from z_server.routers import waitlist as waitlist_router  # noqa: E402


class WaitlistApiTest(unittest.TestCase):
    def setUp(self):
        reset_engine()
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
        if os.path.exists(_DB_PATH):
            os.unlink(_DB_PATH)
        # Reset rate-limit state between tests
        waitlist_router._hits.clear()
        init_db()
        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self):
        reset_engine()
        get_settings.cache_clear()
        waitlist_router._hits.clear()
        if os.path.exists(_DB_PATH):
            try:
                os.unlink(_DB_PATH)
            except OSError:
                pass

    def test_landing_page_served_at_root(self):
        # Jinja fallback when Z_FRONTEND_URL is unset (Next owns these in prod).
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        body = resp.text
        self.assertIn("uncertainty", body.lower())
        self.assertIn("Reusable skills", body)
        self.assertIn("early testing", body.lower())
        self.assertIn("install-cmd-curl", body)
        self.assertIn("install-cmd-pip", body)
        self.assertIn("waitlist-form", body)
        self.assertIn("waitlist-modal", body)
        self.assertIn("data-open-waitlist", body)
        self.assertIn('id="model-access"', body)
        self.assertIn("/pricing", body)
        self.assertIn("You're on the list", body)
        self.assertIn("/static/css/landing.css", body)
        self.assertIn("/static/js/landing.js", body)
        self.assertIn("JetBrains+Mono", body)

    def test_pricing_page_renders(self):
        resp = self.client.get("/pricing")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("Z Router", body)
        self.assertIn("Coming soon", body)
        self.assertIn("Bring your own key", body)
        self.assertIn("data-waitlist-tag=\"router\"", body)
        self.assertIn("waitlist-modal", body)
        self.assertIn("JetBrains+Mono", body)

    def test_public_pages_redirect_when_frontend_url_set(self):
        os.environ["Z_FRONTEND_URL"] = "https://z-agent.dev"
        get_settings.cache_clear()
        try:
            app = create_app()
            client = TestClient(app, follow_redirects=False)
            for path in ("/", "/pricing", "/login"):
                resp = client.get(path)
                self.assertEqual(resp.status_code, 307, path)
                self.assertEqual(
                    resp.headers.get("location"),
                    f"https://z-agent.dev{path if path != '/' else '/'}",
                )
        finally:
            os.environ.pop("Z_FRONTEND_URL", None)
            get_settings.cache_clear()

    def test_static_assets(self):
        css = self.client.get("/static/css/landing.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("#0A0A0A", css.text)
        self.assertIn("#F5F5F5", css.text)
        self.assertIn("#C96A2B", css.text)
        self.assertIn("JetBrains Mono", css.text)
        self.assertIn(".lp-modal", css.text)
        self.assertIn(".lp-model-cards", css.text)
        self.assertIn(".pricing-columns", css.text)
        js = self.client.get("/static/js/landing.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn("/v1/waitlist", js.text)
        self.assertIn("fetch(", js.text)
        self.assertIn("copy-install", js.text)
        self.assertIn("setupWaitlistModal", js.text)
        self.assertIn("setupScrollReveal", js.text)
        self.assertIn("interest", js.text)

    def test_signup_creates_row(self):
        resp = self.client.post(
            "/v1/waitlist",
            json={
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["already_registered"])

    def test_waitlist_accepts_optional_interest_tag(self):
        from sqlalchemy import select

        from z_server.db import SessionLocal
        from z_server.models.waitlist import WaitlistSignup

        resp = self.client.post(
            "/v1/waitlist",
            json={
                "first_name": "Nat",
                "last_name": "Router",
                "email": "router-fan@example.com",
                "interest": "router",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["ok"])

        with SessionLocal() as db:
            row = db.execute(
                select(WaitlistSignup).where(
                    WaitlistSignup.email == "router-fan@example.com"
                )
            ).scalars().first()
            self.assertIsNotNone(row)
            self.assertEqual(row.interest, "router")

        # Omitting interest must not break existing callers.
        resp2 = self.client.post(
            "/v1/waitlist",
            json={
                "first_name": "Plain",
                "last_name": "Signup",
                "email": "plain@example.com",
            },
        )
        self.assertEqual(resp2.status_code, 200, resp2.text)
        with SessionLocal() as db:
            row2 = db.execute(
                select(WaitlistSignup).where(WaitlistSignup.email == "plain@example.com")
            ).scalars().first()
            self.assertIsNotNone(row2)
            self.assertIsNone(row2.interest)

    def test_duplicate_email_is_success(self):
        payload = {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "Ada@Example.com",
        }
        first = self.client.post("/v1/waitlist", json=payload)
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/v1/waitlist",
            json={
                "first_name": "A",
                "last_name": "L",
                "email": "ada@example.com",
            },
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["ok"])
        self.assertTrue(second.json()["already_registered"])

    def test_invalid_email_rejected(self):
        resp = self.client.post(
            "/v1/waitlist",
            json={"first_name": "A", "last_name": "B", "email": "not-an-email"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_name_rejected(self):
        resp = self.client.post(
            "/v1/waitlist",
            json={"first_name": "  ", "last_name": "B", "email": "ok@example.com"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_no_auth_required(self):
        # Explicitly no Authorization header
        resp = self.client.post(
            "/v1/waitlist",
            json={
                "first_name": "Grace",
                "last_name": "Hopper",
                "email": "grace@example.com",
            },
        )
        self.assertEqual(resp.status_code, 200)

    def test_rate_limit(self):
        waitlist_router._RATE_LIMIT = 3
        try:
            for i in range(3):
                r = self.client.post(
                    "/v1/waitlist",
                    json={
                        "first_name": "R",
                        "last_name": "L",
                        "email": f"rate{i}@example.com",
                    },
                )
                self.assertEqual(r.status_code, 200, r.text)
            blocked = self.client.post(
                "/v1/waitlist",
                json={
                    "first_name": "R",
                    "last_name": "L",
                    "email": "rate-blocked@example.com",
                },
            )
            self.assertEqual(blocked.status_code, 429)
        finally:
            waitlist_router._RATE_LIMIT = 10


if __name__ == "__main__":
    unittest.main()
