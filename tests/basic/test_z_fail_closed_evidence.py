"""Fail-closed evidence strategies — architecture, not another one-off detector."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_fail_closed_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.checklist import (  # noqa: E402
    ItemEvidence,
    bind_evidence,
    rescore_checklist_with_evidence,
    rescore_checklist_with_model,
)
from aider.z.uncertainty.evidence_strategy import (  # noqa: E402
    allows_fully,
    combine_model_and_mechanical,
    is_registered_kind,
    status_from_strategy,
)
from aider.z.uncertainty.schema import RequirementItem, TaskChecklist  # noqa: E402


class FailClosedStrategyTest(unittest.TestCase):
    def test_unknown_kind_never_fully(self):
        ev = ItemEvidence(
            item_id="1",
            item_text="Do the mysterious thing",
            kind="vibes_only",
            file_hits=["a.py"],
            symbol_hits=["foo"],
            test_hits=["tests/test_a.py"],
            keyword_hits=["mysterious"],
        )
        self.assertFalse(is_registered_kind("vibes_only"))
        self.assertFalse(allows_fully(ev))
        self.assertEqual(status_from_strategy(ev, ["mysterious"]), "Not Addressed")
        self.assertIn("unsupported_kind:fail_closed", ev.evidence_notes)

    def test_product_fully_needs_hard_triad(self):
        ev = ItemEvidence(
            item_id="1",
            item_text="Implement redact_ipv4",
            kind="product",
            file_hits=["redact.py"],
            symbol_hits=["redact_ipv4"],
            # no tests
        )
        self.assertFalse(allows_fully(ev))
        self.assertEqual(status_from_strategy(ev, ["redact"]), "Partially Addressed")

        ev.test_hits = ["tests/test_redact.py"]
        self.assertTrue(allows_fully(ev))
        self.assertEqual(status_from_strategy(ev, ["redact"]), "Fully Addressed")

    def test_model_cannot_raise_above_mechanical(self):
        ev = ItemEvidence(
            item_id="1",
            item_text="Implement feature",
            kind="product",
            file_hits=["a.py"],
        )
        final, ceilinged = combine_model_and_mechanical(
            "Partially Addressed", "Fully Addressed", ev=ev
        )
        self.assertEqual(final, "Partially Addressed")
        self.assertTrue(ceilinged)
        self.assertIn("model_claimed_above_mechanical_evidence", ev.evidence_notes)

    def test_model_cannot_talk_down_mechanical_fully(self):
        final, ceilinged = combine_model_and_mechanical(
            "Fully Addressed", "Not Addressed"
        )
        self.assertEqual(final, "Fully Addressed")
        self.assertFalse(ceilinged)

    def test_rescore_with_model_respects_ceiling(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="Feat",
            items=[
                RequirementItem(
                    text="Implement FlowGuard allow",
                    kind="product",
                    status="Not Addressed",
                )
            ],
        )
        evidence = bind_evidence(
            checklist,
            files_changed=["flowguard.py"],
            file_contents={"flowguard.py": "class FlowGuard:\n    def allow(self): pass\n"},
            symbols=["FlowGuard", "allow"],
            test_files=[],
        )
        # Model claims Fully — must stay Partial/Not without tests
        def fake_model(_prompt: str) -> str:
            return (
                '{"items":[{"id":"%s","status":"Fully Addressed",'
                '"missing":""}]}' % checklist.items[0].id
            )

        rescore_checklist_with_model(
            checklist, evidence, model_complete=fake_model
        )
        self.assertNotEqual(checklist.items[0].status, "Fully Addressed")
        self.assertIn(
            "model_claimed_above_mechanical_evidence",
            evidence[0].evidence_notes,
        )

    def test_decision_requires_decision_hits_not_process_log(self):
        ev = ItemEvidence(
            item_id="1",
            item_text="Ask me before committing",
            kind="decision",
            log_hits=["verify"],
            verification_ok=True,
        )
        # Process crumbs alone must not clear a decision requirement
        self.assertFalse(allows_fully(ev))
        self.assertEqual(status_from_strategy(ev), "Not Addressed")
        ev.decision_hits = ["confirmed"]
        self.assertTrue(allows_fully(ev))


if __name__ == "__main__":
    unittest.main()
