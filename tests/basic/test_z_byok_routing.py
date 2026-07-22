"""Multi-key BYOK local routing — network-free, reuses aider.z.routing."""

from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path

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
        from aider.z.routing import model_by_id

        os.environ["ANTHROPIC_API_KEY"] = "sk-a"
        os.environ["OPENAI_API_KEY"] = "sk-b"
        chosen = select_local_model(task_mode="ask")
        self.assertIsNotNone(chosen)
        profile = model_by_id(chosen)
        self.assertIn(profile.provider, {"anthropic", "openai"})

    def test_never_offers_an_unconfigured_provider(self):
        from aider.z.byok_routing import select_local_model
        from aider.z.routing import model_by_id

        os.environ["DEEPSEEK_API_KEY"] = "sk-d"
        chosen = select_local_model(task_mode="implement", intent="fix the race condition")
        self.assertIsNotNone(chosen)
        self.assertEqual(model_by_id(chosen).provider, "deepseek")

    def test_domain_soft_preference_via_local_entry_point(self):
        from aider.z.byok_routing import select_local_model
        from aider.z.routing import model_by_id

        os.environ["OPENAI_API_KEY"] = "sk-b"
        chosen = select_local_model(
            task_mode="implement",
            intent="what's the algorithmic complexity of this function",
        )
        # o3-mini is the "reasoning" tagged, math-preferred OpenAI model.
        self.assertEqual(chosen, "o3-mini")
        self.assertEqual(model_by_id(chosen).provider, "openai")

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


if __name__ == "__main__":
    unittest.main()
