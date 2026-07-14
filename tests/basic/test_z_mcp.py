"""Tests for MCP connections API, encryption, and catalog."""

from __future__ import annotations

import os
import tempfile
import unittest

_DB_PATH = tempfile.mktemp(suffix="_z_mcp_test.db")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
os.environ["Z_SECRET_KEY"] = "test-secret-mcp"
os.environ["Z_SERVER_DEV"] = "1"
os.environ["Z_PUBLIC_BASE_URL"] = "http://testserver"

from z_server.config import get_settings  # noqa: E402

get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from z_server.app import create_app  # noqa: E402
from z_server.db import init_db, reset_engine  # noqa: E402
from z_server.services.crypto import decrypt_credentials, encrypt_credentials  # noqa: E402
from z_server.services.mcp_catalog import list_catalog  # noqa: E402


class McpApiTestCase(unittest.TestCase):
    def setUp(self):
        reset_engine()
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
        if os.path.exists(_DB_PATH):
            os.unlink(_DB_PATH)
        init_db()
        self.app = create_app()
        self.client = TestClient(self.app)
        # Dev login → session cookie + bearer token
        resp = self.client.post(
            "/v1/auth/email/start",
            json={"email": "mcp@example.com", "name": "Mcp"},
        )
        self.assertEqual(resp.status_code, 200)
        verify = self.client.post(
            "/v1/auth/email/verify",
            json={"email": "mcp@example.com", "code": "000000", "name": "Mcp"},
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        self.token = verify.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        reset_engine()
        get_settings.cache_clear()
        if os.path.exists(_DB_PATH):
            try:
                os.unlink(_DB_PATH)
            except OSError:
                pass

    def test_catalog(self):
        resp = self.client.get("/v1/mcp/catalog")
        self.assertEqual(resp.status_code, 200)
        tools = resp.json()["tools"]
        names = {t["server_name"] for t in tools}
        self.assertIn("github", names)
        self.assertIn("postgres", names)
        self.assertTrue(len(list_catalog()) >= 3)

    def test_manual_connect_and_runtime_secrets(self):
        resp = self.client.post(
            "/v1/mcp/connect",
            headers=self.headers,
            json={
                "server_name": "slack",
                "scope": "personal",
                "credentials": {"bot_token": "xoxb-secret-token", "team_id": "T123"},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        conn = resp.json()["connection"]
        self.assertEqual(conn["server_name"], "slack")
        self.assertEqual(conn["status"], "connected")
        self.assertNotIn("bot_token", str(conn.get("config")))

        listed = self.client.get("/v1/mcp/connections", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["connections"]), 1)

        runtime = self.client.get("/v1/mcp/runtime", headers=self.headers)
        self.assertEqual(runtime.status_code, 200)
        tools = runtime.json()["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["credentials"]["bot_token"], "xoxb-secret-token")

        disc = self.client.post(
            f"/v1/mcp/connections/{conn['id']}/disconnect",
            headers=self.headers,
        )
        self.assertEqual(disc.status_code, 200)
        listed2 = self.client.get("/v1/mcp/connections", headers=self.headers)
        self.assertEqual(listed2.json()["connections"], [])

    def test_encryption_roundtrip(self):
        token = encrypt_credentials({"api_key": "abc123"})
        self.assertNotIn("abc123", token)
        self.assertEqual(decrypt_credentials(token)["api_key"], "abc123")

    def test_integrations_page_requires_auth(self):
        resp = self.client.get("/app/integrations", follow_redirects=False)
        self.assertIn(resp.status_code, (401, 403))

    def test_integrations_page_with_cookie(self):
        self.client.cookies.set("z_session", self.token)
        resp = self.client.get("/app/integrations")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Integrations", resp.text)
        self.assertIn("Available", resp.text)


if __name__ == "__main__":
    unittest.main()
