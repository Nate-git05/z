"""Coding-quality Priority 1–2: hard ledger evidence, absorbed failure, config, mutation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_cq_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.checklist import (  # noqa: E402
    bind_evidence,
    rescore_checklist_with_evidence,
)
from aider.z.uncertainty.detectors import (  # noqa: E402
    detect_absorbed_failures,
    detect_unvalidated_config,
)
from aider.z.uncertainty.gate import _effective_gate_tier  # noqa: E402
from aider.z.uncertainty.mutation import (  # noqa: E402
    MutationResult,
    _apply_one_mutation,
    mutation_nodes_from_result,
)
from aider.z.uncertainty.risk import collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
)


class HardLedgerEvidenceTest(unittest.TestCase):
    def test_product_needs_file_symbol_and_test(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="Feat",
            items=[
                RequirementItem(
                    text="Implement FlowGuard rate limiter allow",
                    kind="product",
                )
            ],
        )
        # Code only — no tests → Partial, never Fully
        evidence = bind_evidence(
            checklist,
            files_changed=["flowguard/rate_limiter.py"],
            file_contents={
                "flowguard/rate_limiter.py": (
                    "class FlowGuard:\n"
                    "    def allow(self, key): return True\n"
                )
            },
            symbols=["FlowGuard", "allow"],
            test_files=[],
        )
        rescore_checklist_with_evidence(checklist, evidence)
        self.assertEqual(checklist.items[0].status, "Partially Addressed")
        self.assertIn("test", evidence[0].missing or "")
        self.assertFalse(evidence[0].has_hard_product_evidence)

        # Full triad → Fully
        checklist.items[0].status = "Not Addressed"
        evidence2 = bind_evidence(
            checklist,
            files_changed=["flowguard/rate_limiter.py", "tests/test_flowguard.py"],
            file_contents={
                "flowguard/rate_limiter.py": (
                    "class FlowGuard:\n"
                    "    def allow(self, key): return True\n"
                ),
                "tests/test_flowguard.py": "def test_allow(): assert True\n",
            },
            symbols=["FlowGuard", "allow"],
            test_files=["tests/test_flowguard.py"],
        )
        rescore_checklist_with_evidence(checklist, evidence2)
        self.assertEqual(checklist.items[0].status, "Fully Addressed")
        self.assertTrue(evidence2[0].has_hard_product_evidence)


class AbsorbedFailureTest(unittest.TestCase):
    def test_broad_except_near_import_is_high(self):
        text = (
            "import requests\n"
            "\n"
            "def fetch(url):\n"
            "    try:\n"
            "        return requests.get(url)\n"
            "    except Exception:\n"
            "        return None\n"
        )
        sig = collect_base_signals(["client.py"])
        nodes = detect_absorbed_failures(
            sig,
            file_contents={"client.py": text},
            diff="",  # no diff → still pairs in-file
        )
        self.assertTrue(nodes)
        self.assertEqual(nodes[0].type, NodeType.ABSORBED_FAILURE)
        self.assertEqual(nodes[0].risk_tier, Tier.HIGH)
        self.assertEqual(_effective_gate_tier(nodes[0]), Tier.HIGH)


class UnvalidatedConfigTest(unittest.TestCase):
    def test_init_numeric_without_validation(self):
        text = (
            "class Limiter:\n"
            "    def __init__(self, timeout: float, max_retries: int = 3):\n"
            "        self.timeout = timeout\n"
            "        self.max_retries = max_retries\n"
            "\n"
            "    def run(self):\n"
            "        return self.timeout\n"
        )
        sig = collect_base_signals(["limiter.py"])
        nodes = detect_unvalidated_config(sig, file_contents={"limiter.py": text})
        self.assertTrue(nodes)
        self.assertEqual(nodes[0].type, NodeType.UNVALIDATED_CONFIG)
        self.assertIn("timeout", nodes[0].signals.get("unvalidated_params") or [])

    def test_init_with_validation_skipped(self):
        text = (
            "class Limiter:\n"
            "    def __init__(self, timeout: float):\n"
            "        if timeout <= 0:\n"
            "            raise ValueError('timeout')\n"
            "        self.timeout = timeout\n"
        )
        sig = collect_base_signals(["limiter.py"])
        nodes = detect_unvalidated_config(sig, file_contents={"limiter.py": text})
        self.assertEqual(nodes, [])


class MutationHelperTest(unittest.TestCase):
    def test_apply_mutation(self):
        out = _apply_one_mutation("    if x > 0:")
        self.assertIsNotNone(out)
        new_line, label = out
        self.assertIn(">=", new_line)
        self.assertIn(">", label)

    def test_survivor_node(self):
        result = MutationResult(
            ran=True,
            attempted=2,
            killed=1,
            survivors=[
                {
                    "file": "mod.py",
                    "line": 10,
                    "mutation": "> → >=",
                    "original": "if x > 0:",
                    "mutant": "if x >= 0:",
                }
            ],
        )
        sig = collect_base_signals(["mod.py"])
        nodes = mutation_nodes_from_result(result, signals=sig)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].type, NodeType.WEAK_TEST)
        self.assertEqual(_effective_gate_tier(nodes[0]), Tier.HIGH)


class GatedPlanningTest(unittest.TestCase):
    def test_routine_request_skips_plan(self):
        from aider.z.uncertainty.plan import triage_for_planning

        required, reason, _ = triage_for_planning(
            ["utils/format.py"],
            user_text="Rename the helper and add a docstring.",
        )
        self.assertFalse(required)
        self.assertEqual(reason, "")

    def test_high_stakes_request_requires_plan(self):
        from aider.z.uncertainty.plan import (
            draft_plan_from_request,
            format_plan_for_context,
            triage_for_planning,
        )

        required, reason, sig = triage_for_planning(
            ["billing/checkout.py"],
            user_text="Add payment timeout and rate limit validation for checkout.",
        )
        self.assertTrue(required)
        self.assertTrue(sig.high_stakes_hit or "high_stakes" in reason or "request" in reason)

        plan = draft_plan_from_request(
            "Add payment timeout and rate limit validation for checkout.",
            title="Checkout hardening",
            reason=reason,
            files=["billing/checkout.py"],
        )
        self.assertTrue(plan.validation_contracts)
        names = {c.input_name for c in plan.validation_contracts}
        self.assertTrue({"timeout", "rate_limit"} & names or "timeout" in names)
        self.assertTrue(plan.invariants)
        ctx = format_plan_for_context(plan)
        self.assertIn("Validation contracts", ctx)
        self.assertIn("Invariants", ctx)

    def test_blast_radius_triggers_plan(self):
        from aider.z.uncertainty.plan import triage_for_planning

        required, reason, _ = triage_for_planning(
            ["lib/shared.py"],
            user_text="Tweak the helper slightly.",
            reference_count=12,
            blast_radius_threshold=5,
        )
        self.assertTrue(required)
        self.assertIn("blast_radius", reason)

    def test_approve_unblocks_edits(self):
        from aider.z.uncertainty.engine import SessionContext, UncertaintyEngine
        from aider.z.uncertainty.store import UncertaintyStore

        root = Path(tempfile.mkdtemp(prefix="z_plan_"))
        store = UncertaintyStore(root=root, repo_key=str(root))
        eng = UncertaintyEngine(SessionContext(root=root, store=store))
        eng.begin_task("Harden auth session token validation.")
        plan = eng.maybe_require_plan(
            "Harden auth session token validation.",
            files=["auth/session.py"],
        )
        self.assertIsNotNone(plan)
        self.assertTrue(eng.edits_blocked_pending_plan())
        eng.approve_plan(plan)
        self.assertFalse(eng.edits_blocked_pending_plan())
        self.assertTrue(eng.ctx.plan_approved)


if __name__ == "__main__":
    unittest.main()
