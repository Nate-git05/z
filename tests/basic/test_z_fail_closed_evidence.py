"""Fail-closed evidence strategies — exhaustive kind→verifier registry."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_fail_closed_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.absorption_taxonomy import (  # noqa: E402
    ABSORPTION_TAXONOMY,
    scan_failure_absorption,
    taxonomy_pattern_ids,
)
from aider.z.uncertainty.checklist import (  # noqa: E402
    ItemEvidence,
    bind_evidence,
    rescore_checklist_with_evidence,
    rescore_checklist_with_model,
)
from aider.z.uncertainty.detectors import (  # noqa: E402
    detect_failure_absorption,
    detect_requirement_gaps,
)
from aider.z.uncertainty.evidence_strategy import (  # noqa: E402
    ALL_REQUIREMENT_KINDS,
    KIND_VERIFIERS,
    STATUS_UNVERIFIABLE,
    allows_fully,
    combine_model_and_mechanical,
    hard_block_kind,
    is_registered_kind,
    status_from_strategy,
    verifier_for,
)
from aider.z.uncertainty.gate import _effective_gate_tier  # noqa: E402
from aider.z.uncertainty.risk import collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
)


class ExhaustiveRegistryTest(unittest.TestCase):
    def test_registry_covers_every_kind(self):
        self.assertEqual(set(KIND_VERIFIERS), set(ALL_REQUIREMENT_KINDS))
        for kind, row in KIND_VERIFIERS.items():
            # Every row is either a real verifier or explicit absence
            if row.has_verifier:
                self.assertIsNotNone(row.allows_fully)
                self.assertIsNotNone(row.status_fn)
            else:
                self.assertIsNone(row.allows_fully)
                self.assertIsNone(row.status_fn)

    def test_unknown_kind_is_unverifiable_not_silent_pass(self):
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
        self.assertEqual(status_from_strategy(ev, ["mysterious"]), STATUS_UNVERIFIABLE)
        self.assertIn("unverifiable:vibes_only", ev.evidence_notes)
        self.assertIn("absence_of_verifier", ev.evidence_notes)
        self.assertFalse(hard_block_kind("vibes_only"))

    def test_external_assumption_is_explicit_absence(self):
        v = verifier_for("external_assumption")
        self.assertFalse(v.has_verifier)
        ev = ItemEvidence(
            item_id="1",
            item_text="Confirm the upstream API returns 200",
            kind="external_assumption",
        )
        self.assertEqual(status_from_strategy(ev), STATUS_UNVERIFIABLE)

    def test_product_fully_needs_hard_triad(self):
        ev = ItemEvidence(
            item_id="1",
            item_text="Implement redact_ipv4",
            kind="product",
            file_hits=["redact.py"],
            symbol_hits=["redact_ipv4"],
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

    def test_model_cannot_clear_unverifiable(self):
        final, ceilinged = combine_model_and_mechanical(
            STATUS_UNVERIFIABLE, "Fully Addressed"
        )
        self.assertEqual(final, STATUS_UNVERIFIABLE)
        self.assertTrue(ceilinged)

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
            file_contents={
                "flowguard.py": "class FlowGuard:\n    def allow(self): pass\n"
            },
            symbols=["FlowGuard", "allow"],
            test_files=[],
        )

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

    def test_checklist_rescore_survives_stray_brace_in_model_response(self):
        """Incidental braces in model prose must not discard a valid JSON score.

        Fail-closed ceilings may keep status at mechanical Not Addressed when
        the model claims Partial/Full without evidence — but the parsed
        ``missing`` field must still be applied. The old greedy ``{...}``
        regex raised JSONDecodeError and silently left evidence untouched.
        """
        checklist = TaskChecklist(
            task_id="t1",
            title="Concurrency",
            items=[
                RequirementItem(
                    id="req_1",
                    text="Protect shared state with a mutex",
                    kind="product",
                    status="Not Addressed",
                )
            ],
        )
        evidence = bind_evidence(
            checklist,
            files_changed=["sync.cpp"],
            file_contents={"sync.cpp": "void lock() {}\n"},
            symbols=[],
            test_files=[],
        )
        prior_missing = evidence[0].missing

        def fake_model(_prompt: str) -> str:
            return (
                "The mutex pattern here uses std::lock_guard{mtx} for safety.\n\n"
                '{"items": [{"id": "req_1", "status": "Partially Addressed", '
                '"evidence": ["lock present"], "missing": "no test yet"}]}'
            )

        rescore_checklist_with_model(
            checklist, evidence, model_complete=fake_model
        )
        self.assertEqual(evidence[0].missing, "no test yet")
        self.assertNotEqual(evidence[0].missing, prior_missing)

    def test_decision_requires_decision_hits_not_process_log(self):
        ev = ItemEvidence(
            item_id="1",
            item_text="Ask me before committing",
            kind="decision",
            log_hits=["verify"],
            verification_ok=True,
        )
        self.assertFalse(allows_fully(ev))
        self.assertEqual(status_from_strategy(ev), "Not Addressed")
        ev.decision_hits = ["confirmed"]
        self.assertTrue(allows_fully(ev))

    def test_unverifiable_gap_is_low_informational(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="API",
            items=[
                RequirementItem(
                    text="Confirm live API returns pagination cursor",
                    kind="external_assumption",
                    status="Not Addressed",
                )
            ],
        )
        evidence = bind_evidence(
            checklist,
            files_changed=["client.py"],
            file_contents={"client.py": "def fetch(): pass\n"},
        )
        rescore_checklist_with_evidence(checklist, evidence)
        self.assertEqual(checklist.items[0].status, STATUS_UNVERIFIABLE)
        self.assertIn("Unverifiable — no check exists", evidence[0].missing or "")

        sig = collect_base_signals(["client.py"])
        nodes = detect_requirement_gaps(sig, checklist=checklist)
        self.assertEqual(len(nodes), 1)
        self.assertTrue(nodes[0].signals.get("unverifiable"))
        self.assertEqual(nodes[0].risk_tier, Tier.LOW)
        self.assertEqual(_effective_gate_tier(nodes[0]), Tier.LOW)


class AbsorptionTaxonomyTest(unittest.TestCase):
    def test_taxonomy_has_named_patterns(self):
        ids = set(taxonomy_pattern_ids())
        self.assertIn("getattr_new_param_default", ids)
        self.assertIn("bare_except_pass", ids)
        self.assertIn("except_pass_block", ids)
        self.assertIn("dict_get_masking_default", ids)
        # Only getattr is hard-block today
        getattr_row = next(
            p for p in ABSORPTION_TAXONOMY if p.pattern_id == "getattr_new_param_default"
        )
        self.assertTrue(getattr_row.hard_block)
        for p in ABSORPTION_TAXONOMY:
            if p.pattern_id != "getattr_new_param_default":
                self.assertFalse(p.hard_block)

    def test_scanner_finds_multiple_shapes(self):
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,3 +1,8 @@\n"
            "+try:\n"
            "+    do()\n"
            "+except Exception:\n"
            "+    pass\n"
            "+val = cfg.get('token', None)\n"
            "+flag = enabled or False\n"
        )
        hits = scan_failure_absorption(diff)
        ids = {h.pattern_id for h in hits}
        self.assertIn("except_pass_block", ids)
        self.assertIn("dict_get_masking_default", ids)
        self.assertIn("or_falsey_default", ids)

    def test_detector_emits_informational_for_new_shapes(self):
        diff = (
            "diff --git a/mod.py b/mod.py\n"
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,2 +1,5 @@\n"
            "+try:\n"
            "+    risky()\n"
            "+except ValueError:\n"
            "+    return None\n"
        )
        sig = collect_base_signals(["mod.py"])
        nodes = detect_failure_absorption(
            sig, file_contents={"mod.py": "x=1\n"}, diff=diff
        )
        abs_nodes = [n for n in nodes if n.type == NodeType.FAILURE_ABSORPTION]
        self.assertTrue(abs_nodes)
        self.assertFalse(abs_nodes[0].signals.get("absorption_hard_block"))
        self.assertEqual(_effective_gate_tier(abs_nodes[0]), Tier.MEDIUM)

    def test_getattr_guidance_presents_both_explanations(self):
        """Do not assert incomplete-wiring as fact — keep High hard-block."""
        diff = (
            "diff --git a/event_queue.py b/event_queue.py\n"
            "--- a/event_queue.py\n"
            "+++ b/event_queue.py\n"
            "@@ -1,6 +1,9 @@\n"
            " class Event:\n"
            "-    def __init__(self, name):\n"
            '+    def __init__(self, name, priority="normal"):\n'
            "         self.name = name\n"
            "+        self.priority = priority\n"
            " def process(event):\n"
            '+    return getattr(event, "priority", "normal")\n'
        )
        contents = {
            "event_queue.py": (
                "class Event:\n"
                '    def __init__(self, name, priority="normal"):\n'
                "        self.name = name\n"
                "        self.priority = priority\n"
                "def process(event):\n"
                '    return getattr(event, "priority", "normal")\n'
            )
        }
        sig = collect_base_signals(["event_queue.py"])
        nodes = detect_failure_absorption(
            sig, file_contents=contents, diff=diff
        )
        getattr_nodes = [
            n
            for n in nodes
            if n.signals.get("absorption_pattern_id") == "getattr_new_param_default"
        ]
        self.assertTrue(getattr_nodes)
        node = getattr_nodes[0]
        self.assertTrue(node.signals.get("absorption_hard_block"))
        self.assertEqual(node.risk_tier, Tier.HIGH)
        blob = " ".join(
            [
                node.explanation or "",
                node.why_uncertain or "",
                node.suggested_fix or "",
                node.suggested_prompt or "",
            ]
        )
        self.assertIn("incomplete wiring", blob.lower())
        self.assertIn("compatibility", blob.lower())
        self.assertNotIn(
            "Fix the outdated test helper/fixture instead of weakening the contract",
            node.explanation or "",
        )


if __name__ == "__main__":
    unittest.main()
