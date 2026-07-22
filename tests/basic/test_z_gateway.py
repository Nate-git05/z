"""Tests for Z routing gateway (Phase 1)."""

from __future__ import annotations

import os
import tempfile
import unittest

_DB_PATH = tempfile.mktemp(suffix="_z_gateway_test.db")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
os.environ["Z_SECRET_KEY"] = "test-secret-gateway"
os.environ["Z_SERVER_DEV"] = "1"
os.environ["Z_PUBLIC_BASE_URL"] = "http://testserver"
os.environ["Z_GATEWAY_STUB"] = "1"
# Ensure no real upstream key interferes
os.environ.pop("Z_GATEWAY_OPENAI_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

from z_server.config import get_settings  # noqa: E402

get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from z_server.app import create_app  # noqa: E402
from z_server.db import init_db, reset_engine  # noqa: E402


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        reset_engine()
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
        os.environ["Z_GATEWAY_STUB"] = "1"
        if os.path.exists(_DB_PATH):
            os.unlink(_DB_PATH)
        init_db()
        self.app = create_app()
        self.client = TestClient(self.app, raise_server_exceptions=True)
        self.token = self._login()

    def tearDown(self):
        reset_engine()
        get_settings.cache_clear()
        if os.path.exists(_DB_PATH):
            try:
                os.unlink(_DB_PATH)
            except OSError:
                pass

    def _login(self) -> str:
        self.client.post(
            "/v1/auth/email/start",
            json={"email": "gw@example.com", "name": "Gw"},
        )
        verify = self.client.post(
            "/v1/auth/email/verify",
            json={"email": "gw@example.com", "code": "123456", "name": "Gw"},
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        return verify.json()["access_token"]

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_health(self):
        resp = self.client.get("/v1/gateway/health")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(resp.json()["policy"], "v1-taskmode")

    def test_chat_completions_requires_auth(self):
        # Fresh client — setUp login leaves a session cookie on self.client.
        anon = TestClient(self.app, raise_server_exceptions=True)
        resp = anon.post(
            "/v1/gateway/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_chat_completions_stub_and_usage(self):
        resp = self.client.post(
            "/v1/gateway/chat/completions",
            headers=self._auth(),
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello gateway"}],
                "thread_id": "t-1",
                "task_mode": "ask",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["object"], "chat.completion")
        self.assertIn("stub", data["choices"][0]["message"]["content"].lower())
        self.assertIn("z_routing", data)
        routed_model = data["z_routing"]["model_id"]

        usage = self.client.get("/v1/gateway/usage", headers=self._auth())
        self.assertEqual(usage.status_code, 200, usage.text)
        body = usage.json()
        self.assertGreaterEqual(body["total_requests"], 1)
        self.assertTrue(any(r["model_id"] == routed_model for r in body["by_model"]))

    def test_resolve_route(self):
        from z_server.services.gateway_proxy import resolve_route

        r = resolve_route(
            "openai/gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            task_mode="ask",
        )
        self.assertTrue(r["upstream_model"])
        self.assertEqual(r["routing_policy_version"], "v1-taskmode")
        self.assertEqual(r["base_tier"], "trivial")


if __name__ == "__main__":
    unittest.main()
