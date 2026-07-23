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

    def test_resolve_route_derives_domain_from_intent_when_omitted(self):
        from z_server.services.gateway_proxy import resolve_route

        r = resolve_route(
            "openai/gpt-4o",
            messages=[{"role": "user", "content": "fix the race condition in the pool"}],
            task_mode="implement",
        )
        self.assertEqual(r.get("domain"), "concurrency")

    def test_duplicate_price_dict_removed(self):
        """Regression guard: pricing is unified through aider.z.routing's
        PricingCache — the old hand-maintained, drift-prone dict must not
        silently come back."""
        import z_server.services.gateway_proxy as gp

        self.assertFalse(hasattr(gp, "_PRICE_PER_MTOK"))

    def test_estimate_cost_usd_matches_pricing_cache(self):
        from aider.z.routing import PricingCache, model_by_id
        from z_server.services.gateway_proxy import _estimate_cost_usd

        profile = model_by_id("claude-haiku-4-5")
        expected = PricingCache().estimate_call_cost(profile, tokens_in=1000, tokens_out=500)
        self.assertAlmostEqual(_estimate_cost_usd("claude-haiku-4-5", 1000, 500), expected)


class MultiProviderUpstreamTest(unittest.TestCase):
    """Part C — generic litellm-backed upstream call for non-OpenAI providers."""

    def setUp(self):
        for var in (
            "Z_GATEWAY_OPENAI_API_KEY", "OPENAI_API_KEY",
            "Z_GATEWAY_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
            "Z_GATEWAY_GOOGLE_API_KEY", "GEMINI_API_KEY",
            "Z_GATEWAY_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY",
            "Z_GATEWAY_GROQ_API_KEY", "GROQ_API_KEY",
        ):
            os.environ.pop(var, None)

    def test_missing_key_raises_provider_specific_error(self):
        from z_server.services.gateway_proxy import (
            GatewayUpstreamError,
            _call_upstream_via_litellm,
        )

        with self.assertRaises(GatewayUpstreamError) as ctx:
            _call_upstream_via_litellm(
                provider="anthropic", upstream_model="claude-sonnet-5",
                messages=[{"role": "user", "content": "hi"}],
                temperature=None, max_tokens=None,
            )
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("Z_GATEWAY_ANTHROPIC_API_KEY", ctx.exception.message)

    def test_dispatch_calls_litellm_with_provider_prefix_and_key(self):
        import unittest.mock as mock

        from z_server.services.gateway_proxy import _call_upstream

        os.environ["Z_GATEWAY_ANTHROPIC_API_KEY"] = "sk-test-anthropic"
        fake_response = mock.MagicMock()
        fake_response.model_dump.return_value = {"id": "x", "choices": []}

        with mock.patch("litellm.completion", return_value=fake_response) as m:
            body = _call_upstream(
                provider="anthropic", upstream_model="claude-sonnet-5",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.2, max_tokens=100,
            )
        self.assertEqual(body, {"id": "x", "choices": []})
        _, kwargs = m.call_args
        self.assertEqual(kwargs["model"], "anthropic/claude-sonnet-5")
        self.assertEqual(kwargs["api_key"], "sk-test-anthropic")
        self.assertEqual(kwargs["temperature"], 0.2)
        self.assertEqual(kwargs["max_tokens"], 100)

    def test_openai_still_uses_direct_httpx_path_not_litellm(self):
        import unittest.mock as mock

        from z_server.services.gateway_proxy import _call_upstream

        with mock.patch("litellm.completion") as fake_litellm:
            with self.assertRaises(Exception):
                # No key configured -> raises before any HTTP call; the
                # assertion that matters is litellm.completion is untouched.
                _call_upstream(
                    provider="openai", upstream_model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "hi"}],
                    temperature=None, max_tokens=None,
                )
        fake_litellm.assert_not_called()

    def test_each_non_openai_provider_gets_correct_litellm_prefix(self):
        import unittest.mock as mock

        from z_server.services.gateway_proxy import _call_upstream

        cases = {
            "anthropic": ("Z_GATEWAY_ANTHROPIC_API_KEY", "anthropic/claude-sonnet-5"),
            "google": ("Z_GATEWAY_GOOGLE_API_KEY", "gemini/gemini-1.5-pro"),
            "deepseek": ("Z_GATEWAY_DEEPSEEK_API_KEY", "deepseek/deepseek-v3"),
            "groq": ("Z_GATEWAY_GROQ_API_KEY", "groq/groq-llama-70b"),
        }
        for provider, (env_var, expected_model) in cases.items():
            with self.subTest(provider=provider):
                os.environ[env_var] = "sk-test"
                fake_response = mock.MagicMock()
                fake_response.model_dump.return_value = {"id": provider, "choices": []}
                with mock.patch("litellm.completion", return_value=fake_response) as m:
                    upstream_model = expected_model.split("/", 1)[1]
                    _call_upstream(
                        provider=provider, upstream_model=upstream_model,
                        messages=[{"role": "user", "content": "hi"}],
                        temperature=None, max_tokens=None,
                    )
                self.assertEqual(m.call_args.kwargs["model"], expected_model)
                os.environ.pop(env_var, None)


if __name__ == "__main__":
    unittest.main()
