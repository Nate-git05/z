"""P0 structural edge cases + P1 disposition telemetry."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_unc_edges_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.detectors import detect_edge_cases  # noqa: E402
from aider.z.uncertainty.edges import (  # noqa: E402
    extract_structural_branches,
    parse_changed_lines_from_diff,
    select_undiscussed_branches,
)
from aider.z.uncertainty.gate import record_acceptances  # noqa: E402
from aider.z.uncertainty.outcomes import (  # noqa: E402
    format_stats,
    load_outcomes,
    override_rate,
    record_outcome,
    reset_outcomes,
)
from aider.z.uncertainty.risk import collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeStatus,
    NodeType,
    Tier,
    UncertaintyNode,
)
from aider.z.uncertainty.store import UncertaintyStore  # noqa: E402


SAMPLE = '''\
def checkout(cart, coupon):
    if cart is None:
        return None
    if not cart.items:
        return {"ok": False, "reason": "empty"}
    if coupon == "":
        total = cart.total
    else:
        total = cart.total - coupon.amount
    try:
        charge(total)
    except Exception:
        return {"ok": False}
    return {"ok": True, "total": total}
'''


class StructuralEdgesTest(unittest.TestCase):
    def test_extracts_none_empty_else_except(self):
        branches = extract_structural_branches("cart.py", SAMPLE)
        kinds = {b.kind for b in branches}
        self.assertIn("none_check", kinds)
        self.assertIn("falsy_guard", kinds)
        self.assertIn("empty_check", kinds)
        self.assertIn("else", kinds)
        self.assertIn("except", kinds)
        self.assertTrue(any(b.enclosing == "checkout" for b in branches))

    def test_undiscussed_when_model_silent(self):
        branches = extract_structural_branches("cart.py", SAMPLE)
        kept = select_undiscussed_branches(branches, discussed_text="", test_blob="")
        self.assertGreaterEqual(len(kept), 1)
        # Silence from the model must not clear structural edges
        self.assertTrue(any(b.kind in ("else", "except", "none_check") for b in kept))

    def test_discussed_branch_skipped(self):
        branches = extract_structural_branches("cart.py", SAMPLE)
        discussed = "We considered the empty cart items case and the None cart guard."
        kept = select_undiscussed_branches(
            branches, discussed_text=discussed, test_blob=""
        )
        # empty/none related should drop; else/except may remain
        kinds_left = {b.kind for b in kept}
        self.assertNotIn("none_check", kinds_left)

    def test_test_blob_skips_enclosing(self):
        branches = extract_structural_branches("cart.py", SAMPLE)
        kept = select_undiscussed_branches(
            branches,
            discussed_text="",
            test_blob="def test_checkout():\n    assert checkout(None) is None\n",
        )
        self.assertEqual(kept, [])

    def test_detect_edge_cases_structural_without_model_list(self):
        """Empty model list must NOT yield zero EDGE_CASE nodes when branches exist."""
        reset_outcomes()
        sig = collect_base_signals(["cart.py"])
        nodes = detect_edge_cases(
            sig,
            edge_cases=[],  # model silent — the old failure mode
            file_contents={"cart.py": SAMPLE},
            discussed_text="",
            test_blob="",
        )
        self.assertGreaterEqual(len(nodes), 1)
        self.assertTrue(all(n.type == NodeType.EDGE_CASE for n in nodes))
        self.assertTrue(
            any(n.signals.get("edge_source") == "structural" for n in nodes)
        )

    def test_model_list_still_supplements(self):
        sig = collect_base_signals(["cart.py"])
        nodes = detect_edge_cases(
            sig,
            edge_cases=["Double-click submit races the charge"],
            file_contents={"cart.py": "def f():\n    return 1\n"},
            discussed_text="",
        )
        self.assertTrue(
            any(
                n.signals.get("edge_source") == "model"
                and "Double-click" in n.title
                for n in nodes
            )
        )

    def test_parse_diff_changed_lines(self):
        diff = """\
--- a/cart.py
+++ b/cart.py
@@ -1,3 +1,6 @@
 def checkout(cart):
+    if cart is None:
+        return None
     return cart.total
"""
        lines = parse_changed_lines_from_diff(diff)
        self.assertIn("cart.py", lines)
        self.assertIn(2, lines["cart.py"])


class OutcomesTelemetryTest(unittest.TestCase):
    def setUp(self):
        reset_outcomes()

    def test_record_and_format_stats(self):
        record_outcome(NodeType.EDGE_CASE, "created")
        record_outcome(NodeType.EDGE_CASE, "created")
        record_outcome(NodeType.EDGE_CASE, "ignored")
        record_outcome(NodeType.EDGE_CASE, "force_override")
        record_outcome(NodeType.MISSING_TEST, "created")
        record_outcome(NodeType.MISSING_TEST, "resolved")

        data = load_outcomes()
        edge = data["by_detector"][NodeType.EDGE_CASE.value]
        self.assertEqual(edge["created"], 2)
        self.assertEqual(edge["ignored"], 1)
        self.assertEqual(edge["force_override"], 1)
        self.assertGreater(override_rate(edge), 0.5)

        text = format_stats()
        self.assertIn("Edge Case Blind Spot", text)
        self.assertIn("ovr%", text)

    def test_store_hooks_created_and_ignored(self):
        root = Path(tempfile.mkdtemp(prefix="z_store_out_"))
        store = UncertaintyStore(root=root, repo_key="test-repo")
        node = UncertaintyNode(
            title="t",
            type=NodeType.FRAGILE_LOGIC,
            confidence_tier=Tier.MEDIUM,
            risk_tier=Tier.MEDIUM,
            summary="s",
        )
        store.add(node)
        store.update_status(node.id, NodeStatus.IGNORED)

        data = load_outcomes()
        bucket = data["by_detector"][NodeType.FRAGILE_LOGIC.value]
        self.assertGreaterEqual(bucket["created"], 1)
        self.assertGreaterEqual(bucket["ignored"], 1)

    def test_force_override_via_record_acceptances(self):
        root = Path(tempfile.mkdtemp(prefix="z_force_out_"))
        store = UncertaintyStore(root=root, repo_key="force-repo")
        node = UncertaintyNode(
            title="high",
            type=NodeType.HIGH_STAKES,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.HIGH,
            summary="s",
        )
        store.add(node)
        record_acceptances(store, [node], "force_override")
        data = load_outcomes()
        bucket = data["by_detector"][NodeType.HIGH_STAKES.value]
        self.assertGreaterEqual(bucket["force_override"], 1)


if __name__ == "__main__":
    unittest.main()
