"""Change must rewrite the plan — not glue feedback as a Do: step."""

from __future__ import annotations

import unittest

from aider.z.uncertainty.plan import (
    draft_plan_from_request,
    format_plan_for_user,
    revise_plan_with_feedback,
)


_LOGWATCH = (
    "Okay need to build Build a CLI tool called logwatch that tails "
    "one or more log files, matches lines against user-defined rules (regex or "
    "plain substring), and fires an alert (just print to stderr with a "
    "distinct format for now — no email/webhook needed yet) when a rule "
    "matches more than N times within a rolling time window."
)


class PlanChangeReviseTests(unittest.TestCase):
    def test_rust_meta_revision_rewrites_plan(self):
        plan = draft_plan_from_request(_LOGWATCH, reason="request_text_high_stakes")
        revised = revise_plan_with_feedback(
            plan,
            "build this in rust as a software enginerr for meta would",
            original_request=_LOGWATCH,
        )
        approach = (revised.approach or "").lower()
        steps_blob = " ".join(revised.steps).lower()

        self.assertIn("rust", approach)
        self.assertIn("implement in rust", steps_blob)
        self.assertIn("production", steps_blob + " " + approach)
        # Must NOT echo the Change text as a product step
        self.assertNotIn("build this in rust as a software", steps_blob)
        self.assertFalse(
            any(
                (s or "").lower().startswith("do:") and "rust" in (s or "").lower()
                for s in revised.steps
            )
        )
        # Negated "no email" must not invent an email validation contract
        self.assertFalse(
            any(c.input_name == "email" for c in (revised.validation_contracts or []))
        )
        # Out of scope should mention email/webhook when request forbade them
        oos = " ".join(revised.out_of_scope or []).lower()
        self.assertTrue("email" in oos or "webhook" in oos)

    def test_logwatch_template_not_giant_do_echo(self):
        plan = draft_plan_from_request(_LOGWATCH, reason="request_text_high_stakes")
        self.assertTrue(plan.steps)
        self.assertFalse(all((s or "").lower().startswith("do:") for s in plan.steps))
        self.assertTrue(
            any("rolling" in (s or "").lower() or "window" in (s or "").lower() for s in plan.steps)
        )

    def test_python_socket_still_works(self):
        plan = draft_plan_from_request("build me a slack chatbot")
        revised = revise_plan_with_feedback(
            plan,
            "use socket mode, python, no llm",
            original_request="build me a slack chatbot",
        )
        blob = " ".join(revised.steps).lower() + " " + (revised.approach or "").lower()
        self.assertIn("socket", blob)
        self.assertIn("python", blob)
        self.assertTrue(any("llm" in (o or "").lower() for o in revised.out_of_scope))

    def test_formatted_plan_shows_rust_not_echo(self):
        plan = draft_plan_from_request(_LOGWATCH)
        revised = revise_plan_with_feedback(
            plan,
            "build this in rust",
            original_request=_LOGWATCH,
        )
        body = format_plan_for_user(revised)
        self.assertIn("Rust", body)
        self.assertNotIn("Do: build this in rust", body)


if __name__ == "__main__":
    unittest.main()
