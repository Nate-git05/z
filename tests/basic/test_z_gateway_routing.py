"""Phase 5 — TaskMode → tier selection + escalation on the gateway."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_gw_route_")
os.environ["Z_HOME"] = _HOME
os.environ["Z_GATEWAY_STUB"] = "1"
os.environ["Z_SERVER_DEV"] = "1"
os.environ.pop("Z_GATEWAY_OPENAI_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

from aider.z.routing import CapabilityTier  # noqa: E402
from aider.z.routing.task_tier import (  # noqa: E402
    bump_tier,
    resolve_capability_tier,
    tier_for_task_mode,
    tier_from_intent_text,
)
from z_server.services.gateway_routing import (  # noqa: E402
    ROUTING_POLICY_VERSION,
    resolve_policy_route,
)


class TaskTierMappingTest(unittest.TestCase):
    def test_task_mode_floors(self):
        self.assertEqual(tier_for_task_mode("ask"), CapabilityTier.TRIVIAL)
        self.assertEqual(tier_for_task_mode("implement"), CapabilityTier.MODERATE)
        self.assertEqual(tier_for_task_mode("plan"), CapabilityTier.HARD)

    def test_intent_can_raise_implement_floor(self):
        tier = resolve_capability_tier(
            task_mode="implement",
            intent="fix the race condition in the auth migrator",
        )
        self.assertIn(tier, (CapabilityTier.HARD, CapabilityTier.REASONING_HEAVY))

    def test_bump_tier(self):
        self.assertEqual(
            bump_tier(CapabilityTier.TRIVIAL, 2),
            CapabilityTier.HARD,
        )
        self.assertEqual(
            bump_tier(CapabilityTier.REASONING_HEAVY, 5),
            CapabilityTier.REASONING_HEAVY,
        )

    def test_trivial_intent(self):
        self.assertEqual(
            tier_from_intent_text("rename local variable foo"),
            CapabilityTier.TRIVIAL,
        )


class GatewayPolicyRouteTest(unittest.TestCase):
    def test_ask_selects_cheap_tier(self):
        route = resolve_policy_route(
            preferred_model="gpt-4o",
            messages=[{"role": "user", "content": "what is a mutex?"}],
            task_mode="ask",
            customer_id="test-user",
        )
        self.assertEqual(route["routing_policy_version"], ROUTING_POLICY_VERSION)
        self.assertEqual(route["base_tier"], "trivial")
        # Preferred gpt-4o is HARD-capable; ask floor is trivial so prefer is OK
        self.assertEqual(route["tier"], "trivial")
        self.assertIn(route["model_id"], {"gpt-4o", "gpt-4o-mini", "claude-haiku-4-5", "deepseek-v3", "groq-llama-70b"})

    def test_implement_hard_intent_escalates_model(self):
        route = resolve_policy_route(
            preferred_model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": "fix the race condition in concurrent auth migration",
                }
            ],
            task_mode="implement",
            customer_id="test-user",
        )
        self.assertIn(route["tier"], ("hard", "reasoning_heavy"))
        # gpt-4o-mini is only TRIVIAL — must not stay preferred
        self.assertNotEqual(route["model_id"], "gpt-4o-mini")

    def test_explicit_escalate_bumps_depth(self):
        base = resolve_policy_route(
            preferred_model="gpt-4o-mini",
            messages=[{"role": "user", "content": "add a docstring"}],
            task_mode="implement",
            customer_id="test-user",
        )
        esc = resolve_policy_route(
            preferred_model="gpt-4o-mini",
            messages=[{"role": "user", "content": "add a docstring"}],
            task_mode="implement",
            escalate=True,
            escalation_depth=1,
            customer_id="test-user",
        )
        self.assertTrue(esc["escalated"])
        self.assertGreaterEqual(
            ["trivial", "moderate", "hard", "reasoning_heavy"].index(esc["tier"]),
            ["trivial", "moderate", "hard", "reasoning_heavy"].index(base["tier"]),
        )


class GatewayHttpRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile as tf

        cls._db = tf.mktemp(suffix="_z_gw_p5.db")
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{cls._db}"
        os.environ["Z_SECRET_KEY"] = "test-secret-gateway-p5"
        os.environ["Z_PUBLIC_BASE_URL"] = "http://testserver"
        os.environ["Z_GATEWAY_STUB"] = "1"

        from z_server.config import get_settings

        get_settings.cache_clear()
        from z_server.db import init_db, reset_engine

        reset_engine()
        if os.path.exists(cls._db):
            os.unlink(cls._db)
        init_db()

        from fastapi.testclient import TestClient
        from z_server.app import create_app

        cls.app = create_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.client.post(
            "/v1/auth/email/start",
            json={"email": "p5@example.com", "name": "P5"},
        )
        verify = cls.client.post(
            "/v1/auth/email/verify",
            json={"email": "p5@example.com", "code": "123456", "name": "P5"},
        )
        assert verify.status_code == 200, verify.text
        cls.token = verify.json()["access_token"]

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_health_reports_v1_policy(self):
        resp = self.client.get("/v1/gateway/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["policy"], "v1-taskmode")
        self.assertIn("task_mode", body["features"])

    def test_chat_completions_returns_z_routing(self):
        resp = self.client.post(
            "/v1/gateway/chat/completions",
            headers=self._auth(),
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": "design the system architecture from scratch",
                    }
                ],
                "task_mode": "plan",
                "thread_id": "t-p5",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("z_routing", data)
        self.assertEqual(data["z_routing"]["routing_policy_version"], "v1-taskmode")
        self.assertEqual(data["z_routing"]["task_mode"], "plan")
        self.assertIn(
            data["z_routing"]["tier"],
            ("hard", "reasoning_heavy"),
        )

    def test_routing_outcome_endpoint(self):
        resp = self.client.post(
            "/v1/gateway/routing/outcome",
            headers=self._auth(),
            json={
                "model_id": "gpt-4o",
                "tier": "hard",
                "gate_passed": False,
                "escalated": True,
                "checker_triggered": "high_risk",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["gate_passed"])
        self.assertEqual(body["routing_policy_version"], "v1-taskmode")


class GatewayClientHintsTest(unittest.TestCase):
    def test_extra_body_from_env(self):
        from aider.z.gateway_client import (
            gateway_routing_extra_body,
            set_gateway_routing_hints,
        )

        set_gateway_routing_hints(
            task_mode="implement",
            intent="fix the flaky race",
            escalate=True,
            escalation_depth=1,
            thread_id="thr-1",
        )
        body = gateway_routing_extra_body()
        self.assertEqual(body["task_mode"], "implement")
        self.assertIn("race", body["intent"])
        self.assertTrue(body["escalate"])
        self.assertEqual(body["escalation_depth"], 1)
        self.assertEqual(body["thread_id"], "thr-1")


if __name__ == "__main__":
    unittest.main()
