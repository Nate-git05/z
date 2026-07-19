"""Z model router — select, escalate, calibrate, privacy."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_router_")
os.environ["Z_HOME"] = _HOME

from aider.z.routing import (  # noqa: E402
    MODEL_REGISTRY,
    CalibrationStore,
    CapabilityTier,
    ModelProfile,
    NoEligibleModelError,
    PricingCache,
    ProviderEndpoint,
    RoutingAttempt,
    RoutingOutcomeRecord,
    RoutingPolicy,
    classify_task,
    select_model,
    true_task_cost,
)
from aider.z.routing.calibration import ROUTING_OUTCOME_FIELDS  # noqa: E402
from aider.z.routing.escalate import RoutingTask, run_with_escalation  # noqa: E402
from aider.z.uncertainty.gate import GateResult  # noqa: E402


def _policy(*providers: str, **kw) -> RoutingPolicy:
    endpoints = tuple(
        ProviderEndpoint(provider=p, base_url=f"https://{p}.example", auth_ref=f"ref-{p}")
        for p in providers
    )
    return RoutingPolicy(customer_id="cust-1", allowed_endpoints=endpoints, **kw)


class SelectModelTest(unittest.TestCase):
    def setUp(self):
        self.pricing = PricingCache()
        self.calibration = CalibrationStore(
            path=Path(_HOME) / "cal_select.json", customer_id="cust-1"
        )

    def test_select_model_never_returns_disallowed_provider(self):
        # deepseek is cheapest for TRIVIAL but not on the allowlist
        policy = _policy("anthropic", "google")
        chosen = select_model(
            CapabilityTier.TRIVIAL,
            policy=policy,
            context_tokens=1000,
            latency_budget_ms=None,
            pricing=self.pricing,
            calibration=self.calibration,
        )
        self.assertIn(chosen.provider, {"anthropic", "google"})
        self.assertNotEqual(chosen.provider, "deepseek")

        # Even if we only allow an expensive provider, never pick outside
        policy_anth = _policy("anthropic")
        chosen2 = select_model(
            CapabilityTier.TRIVIAL,
            policy=policy_anth,
            context_tokens=1000,
            latency_budget_ms=None,
            pricing=self.pricing,
            calibration=self.calibration,
        )
        self.assertEqual(chosen2.provider, "anthropic")

    def test_no_eligible_model_raises(self):
        policy = _policy("nonexistent-vendor")
        with self.assertRaises(NoEligibleModelError):
            select_model(
                CapabilityTier.HARD,
                policy=policy,
                context_tokens=1000,
                latency_budget_ms=None,
                pricing=self.pricing,
                calibration=self.calibration,
            )

    def test_context_window_hard_filter(self):
        policy = _policy("groq", "anthropic")
        # groq-llama-70b has 8k context — too small
        chosen = select_model(
            CapabilityTier.TRIVIAL,
            policy=policy,
            context_tokens=50_000,
            latency_budget_ms=None,
            pricing=self.pricing,
            calibration=self.calibration,
        )
        self.assertNotEqual(chosen.model_id, "groq-llama-70b")
        self.assertGreaterEqual(chosen.context_window, 50_000)


class EscalateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="z_esc_"))
        self.pricing = PricingCache()
        self.calibration = CalibrationStore(
            path=Path(_HOME) / "cal_esc.json", customer_id="cust-1"
        )
        self.policy = _policy("anthropic", "deepseek", max_escalations=2)

    def test_escalation_stops_at_max_depth_and_surfaces_to_human(self):
        task = RoutingTask(
            root=self.root,
            request_text="rename a local variable",
            target_files=["foo.py"],
            context_tokens=1000,
            edited_files=["foo.py"],
        )
        calls = {"n": 0}

        def run_model(model, _task):
            calls["n"] += 1
            return ("diff", 0.01)

        def prepare(_coder, _edited):
            return GateResult(allow_commit=False, reason="blocked")

        attempts, result = run_with_escalation(
            task,
            self.policy,
            self.pricing,
            self.calibration,
            run_model=run_model,
            prepare_commit_fn=prepare,
        )
        # max_escalations=2 → 3 attempts (0,1,2)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(calls["n"], 3)
        self.assertFalse(result.allow_commit)
        self.assertTrue(all(not a.gate_passed for a in attempts))

    def test_true_task_cost_sums_all_attempts_not_just_final(self):
        attempts = [
            RoutingAttempt("a", CapabilityTier.TRIVIAL, 0.10, False, escalated_to="moderate"),
            RoutingAttempt("b", CapabilityTier.MODERATE, 0.25, False, escalated_to="hard"),
            RoutingAttempt("c", CapabilityTier.HARD, 0.40, True),
        ]
        self.assertAlmostEqual(true_task_cost(attempts), 0.75)
        # Naive "final only" would be 0.40 — guard the regression explicitly
        self.assertNotAlmostEqual(true_task_cost(attempts), attempts[-1].cost_usd)

    def test_cost_ceiling_blocks_escalation_past_configured_limit(self):
        policy = _policy(
            "anthropic",
            "deepseek",
            max_escalations=5,
            cost_ceiling_per_task_usd=0.05,
        )
        task = RoutingTask(
            root=self.root,
            request_text="tiny tweak",
            target_files=["a.py"],
            context_tokens=1000,
            edited_files=["a.py"],
        )
        costs = [0.04, 0.04, 0.04]  # first ok under ceiling; second would breach

        def run_model(model, _task):
            return ("diff", costs.pop(0) if costs else 0.04)

        def prepare(_coder, _edited):
            return GateResult(allow_commit=False, reason="blocked")

        attempts, result = run_with_escalation(
            task,
            policy,
            self.pricing,
            self.calibration,
            run_model=run_model,
            prepare_commit_fn=prepare,
        )
        self.assertEqual(len(attempts), 1)  # first attempt only
        self.assertFalse(result.allow_commit)
        self.assertIn("cost ceiling", result.reason)
        self.assertLessEqual(true_task_cost(attempts), 0.05 + 1e-9)


class CalibrationTest(unittest.TestCase):
    def test_cold_start_calibration_is_neutral_not_penalized(self):
        store = CalibrationStore(
            path=Path(_HOME) / "cal_cold.json", customer_id="cust-1"
        )
        # Fewer than 10 records → 0.0
        for i in range(5):
            store.record_outcome("deepseek-v3", "trivial", gate_passed=False)
        self.assertEqual(
            store.reliability_penalty("deepseek-v3", "trivial"), 0.0
        )

    def test_penalty_rises_with_failures_after_threshold(self):
        store = CalibrationStore(
            path=Path(_HOME) / "cal_pen.json", customer_id="cust-1"
        )
        for i in range(10):
            store.record_outcome("deepseek-v3", "trivial", gate_passed=(i >= 8))
        penalty = store.reliability_penalty("deepseek-v3", "trivial")
        self.assertGreater(penalty, 0.0)
        self.assertLessEqual(penalty, 1.0)

    def test_routing_outcome_record_never_contains_code_or_prompt_text(self):
        rec = RoutingOutcomeRecord(
            model_id="claude-sonnet-5",
            task_category="hard",
            gate_passed=True,
            escalated=False,
            cost_usd=0.12,
            customer_id="cust-1",
            recorded_at="2026-07-19T00:00:00+00:00",
        )
        CalibrationStore.assert_record_is_metadata_only(rec)
        field_names = set(ROUTING_OUTCOME_FIELDS)
        self.assertEqual(set(rec.__dataclass_fields__) - {"recorded_at"} | {"recorded_at"}, field_names)
        for banned in ("request_text", "diff", "file_contents", "prompt", "code"):
            self.assertNotIn(banned, rec.__dataclass_fields__)


class ClassifyTest(unittest.TestCase):
    def test_high_stakes_request_scores_hard_or_above(self):
        root = Path(tempfile.mkdtemp(prefix="z_cls_"))
        tier = classify_task(
            root,
            "Fix a race condition in the auth payment checkout path",
            ["src/checkout.py"],
        )
        self.assertIn(
            tier,
            (CapabilityTier.HARD, CapabilityTier.REASONING_HEAVY),
        )

    def test_trivial_request_is_moderate_or_below(self):
        root = Path(tempfile.mkdtemp(prefix="z_cls2_"))
        tier = classify_task(root, "Rename local helper foo to bar", ["util.py"])
        self.assertIn(
            tier,
            (CapabilityTier.TRIVIAL, CapabilityTier.MODERATE),
        )


class RegistryTest(unittest.TestCase):
    def test_registry_is_data_rows(self):
        self.assertGreaterEqual(len(MODEL_REGISTRY), 4)
        for m in MODEL_REGISTRY:
            self.assertIsInstance(m, ModelProfile)
            self.assertIsInstance(m.capability_tier, CapabilityTier)


if __name__ == "__main__":
    unittest.main()
