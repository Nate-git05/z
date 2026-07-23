"""Multi-key BYOK local routing — network-free, reuses aider.z.routing."""

from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_HOME = tempfile.mkdtemp(prefix="z_byok_routing_")
os.environ["Z_HOME"] = _HOME

_PROVIDER_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
)


class ByokRoutingTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = {v: os.environ.pop(v, None) for v in _PROVIDER_ENV_VARS}

    def tearDown(self):
        for var, val in self._saved.items():
            os.environ.pop(var, None)
            if val is not None:
                os.environ[var] = val


class ConfiguredProvidersTest(ByokRoutingTestCase):
    def test_reflects_env_state(self):
        from aider.z.byok_routing import configured_byok_providers

        self.assertEqual(configured_byok_providers(), set())
        os.environ["ANTHROPIC_API_KEY"] = "sk-a"
        os.environ["OPENAI_API_KEY"] = "sk-b"
        self.assertEqual(configured_byok_providers(), {"anthropic", "openai"})
        os.environ.pop("OPENAI_API_KEY")
        self.assertEqual(configured_byok_providers(), {"anthropic"})


class SelectLocalModelTest(ByokRoutingTestCase):
    def test_returns_none_with_zero_providers_configured(self):
        from aider.z.byok_routing import select_local_model

        self.assertIsNone(select_local_model(task_mode="ask"))

    def test_picks_cheapest_eligible_among_configured_providers_only(self):
        from aider.z.byok_routing import select_local_model

        os.environ["ANTHROPIC_API_KEY"] = "sk-a"
        os.environ["OPENAI_API_KEY"] = "sk-b"
        choice = select_local_model(task_mode="ask")
        self.assertIsNotNone(choice)
        self.assertIn(choice.provider, {"anthropic", "openai"})
        self.assertEqual(choice.tier, "trivial")

    def test_never_offers_an_unconfigured_provider(self):
        from aider.z.byok_routing import select_local_model

        os.environ["DEEPSEEK_API_KEY"] = "sk-d"
        choice = select_local_model(task_mode="implement", intent="fix the race condition")
        self.assertIsNotNone(choice)
        self.assertEqual(choice.provider, "deepseek")

    def test_domain_soft_preference_via_local_entry_point(self):
        from aider.z.byok_routing import select_local_model

        os.environ["OPENAI_API_KEY"] = "sk-b"
        choice = select_local_model(
            task_mode="implement",
            intent="what's the algorithmic complexity of this function",
        )
        # o3-mini is the "reasoning" tagged, math-preferred OpenAI model.
        self.assertEqual(choice.model_id, "o3-mini")
        self.assertEqual(choice.provider, "openai")

    def test_never_imports_a_networking_library(self):
        """Static guard: this module must have zero network dependency by
        construction — the entire point of local BYOK routing."""
        import aider.z.byok_routing as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        banned = {"httpx", "requests", "urllib", "socket", "aiohttp"}
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(n.name.split(".")[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & banned, imported & banned)


class RecordLocalOutcomeTest(ByokRoutingTestCase):
    def test_writes_under_local_byok_customer_id(self):
        from aider.z.byok_routing import record_local_outcome
        from aider.z.routing import CalibrationStore

        record_local_outcome(model_id="gpt-4o-mini", tier="trivial", gate_passed=True)
        store = CalibrationStore(customer_id="local-byok")
        store._ensure_loaded()
        self.assertTrue(
            any(r.model_id == "gpt-4o-mini" and r.customer_id == "local-byok" for r in store._records)
        )


class _FakeMainModel:
    def __init__(self, name, weak_model_name=None, editor_model_name=None):
        self.name = name
        self.weak_model_name = weak_model_name
        self.editor_model_name = editor_model_name


def _fake_coder():
    from aider.coders.base_coder import Coder

    coder = Coder.__new__(Coder)
    coder.io = MagicMock()
    coder._emit_retained_step = Coder._emit_retained_step.__get__(coder)
    coder._maybe_show_router_request_line = Coder._maybe_show_router_request_line.__get__(coder)
    coder._maybe_route_byok_model = Coder._maybe_route_byok_model.__get__(coder)
    return coder


class RouterRequestLineTest(ByokRoutingTestCase):
    def test_shows_once_per_session_not_every_turn(self):
        coder = _fake_coder()
        coder.main_model = _FakeMainModel("claude-sonnet-5")

        coder._maybe_show_router_request_line()
        coder._maybe_show_router_request_line()

        coder.io.tool_output.assert_called_once_with(
            "→ Routing via Z's router (requested: claude-sonnet-5)"
        )


class MaybeRouteByokModelTest(ByokRoutingTestCase):
    def test_no_op_when_not_byok_mode(self):
        from aider.z.onboarding import save_auth_mode

        save_auth_mode("router")
        coder = _fake_coder()
        coder.main_model = _FakeMainModel("claude-sonnet-5")

        coder._maybe_route_byok_model(
            mode="ask", intent_text=None, domain=None, escalation_depth=0
        )
        coder.io.tool_output.assert_not_called()
        self.assertEqual(coder.main_model.name, "claude-sonnet-5")

    def test_prints_routing_line_and_swaps_model_when_different(self):
        """select_or_prefer never downgrades an already-capable preferred
        model, even for a trivial task — a swap only happens when the
        preferred model doesn't meet the task's tier floor. Start from a
        model too weak for a hard/concurrency task to trigger a real swap."""
        from aider.z.onboarding import save_auth_mode

        save_auth_mode("byok")
        os.environ["ANTHROPIC_API_KEY"] = "sk-a"
        coder = _fake_coder()
        coder.main_model = _FakeMainModel(
            "claude-haiku-4-5", weak_model_name="claude-haiku-4-5"
        )

        coder._maybe_route_byok_model(
            mode="implement",
            intent_text="fix the race condition in the connection pool",
            domain=None,
            escalation_depth=0,
        )

        line = coder.io.tool_output.call_args[0][0]
        self.assertTrue(line.startswith("→ Routing to "))
        self.assertIn("anthropic", line)
        self.assertIn("hard", line)
        self.assertNotEqual(coder.main_model.name, "claude-haiku-4-5")

    def test_pins_weak_model_across_swap(self):
        from aider.z.onboarding import save_auth_mode

        save_auth_mode("byok")
        os.environ["ANTHROPIC_API_KEY"] = "sk-a"
        coder = _fake_coder()
        coder.main_model = _FakeMainModel(
            "claude-haiku-4-5", weak_model_name="claude-haiku-4-5"
        )

        with patch("aider.models.Model") as ModelCls:
            coder._maybe_route_byok_model(
                mode="implement",
                intent_text="fix the race condition in the connection pool",
                domain=None,
                escalation_depth=0,
            )
        _, kwargs = ModelCls.call_args
        self.assertEqual(kwargs.get("weak_model"), "claude-haiku-4-5")


if __name__ == "__main__":
    unittest.main()
