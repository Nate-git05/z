"""Model-authored plan approach/steps text.

draft_plan_from_request()'s approach/steps used to come entirely from a
fixed if/elif chain of canned template paragraphs keyed by regex keyword
matches — e.g. any "build/add + api/backend/server" request got the same
generic "Add or change backend endpoints..." text regardless of the actual
technology mentioned. When a classifier_model is supplied, the weak model
now gets one bounded, fail-safe attempt to author request-specific
approach/steps text instead; any failure keeps today's template result.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

_HOME = tempfile.mkdtemp(prefix="z_plan_clf_")
os.environ["Z_HOME"] = _HOME
os.environ.pop("Z_PLAN_CLASSIFY", None)
os.environ.pop("Z_PLAN_CLASSIFY_TIMEOUT", None)

from aider.z.uncertainty.intent import extract_intent  # noqa: E402
from aider.z.uncertainty.plan import (  # noqa: E402
    _draft_via_model,
    draft_plan_from_request,
)

_RUST_HTTP_REQUEST = "i want to build a http server in rust"


class _FakeClassifierModel:
    def __init__(self, response=None, *, raise_exc=None, hang=False):
        self.response = response
        self.raise_exc = raise_exc
        self.hang = hang
        self.calls = []

    def simple_send_with_retries(self, messages):
        self.calls.append(messages)
        if self.hang:
            time.sleep(30)
        if self.raise_exc:
            raise self.raise_exc
        return self.response


_GOOD_RESPONSE = (
    "APPROACH: Build an async HTTP server in Rust using a framework like axum "
    "or actix-web, with routes for the required endpoints.\n"
    "STEPS:\n"
    "1. Set up a new Cargo project and add the chosen HTTP framework as a dependency.\n"
    "2. Define the router and request handlers for each endpoint.\n"
    "3. Wire up request/response (de)serialization with serde.\n"
    "4. Add a basic integration test that starts the server and hits an endpoint.\n"
)


class DraftViaModelParsingTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_PLAN_CLASSIFY", None)
        os.environ.pop("Z_PLAN_CLASSIFY_TIMEOUT", None)

    def test_no_classifier_model_returns_none(self):
        intent = extract_intent(_RUST_HTTP_REQUEST)
        self.assertIsNone(_draft_via_model(_RUST_HTTP_REQUEST, intent, None))

    def test_valid_response_is_parsed(self):
        intent = extract_intent(_RUST_HTTP_REQUEST)
        fake = _FakeClassifierModel(response=_GOOD_RESPONSE)
        result = _draft_via_model(_RUST_HTTP_REQUEST, intent, fake)
        self.assertIsNotNone(result)
        approach, steps = result
        self.assertIn("Rust", approach)
        self.assertEqual(len(steps), 4)
        self.assertIn("Cargo", steps[0])
        self.assertEqual(len(fake.calls), 1)

    def test_missing_approach_line_falls_back(self):
        intent = extract_intent(_RUST_HTTP_REQUEST)
        fake = _FakeClassifierModel(response="STEPS:\n1. Do a thing.\n2. Do another.\n")
        self.assertIsNone(_draft_via_model(_RUST_HTTP_REQUEST, intent, fake))

    def test_no_numbered_steps_falls_back(self):
        intent = extract_intent(_RUST_HTTP_REQUEST)
        fake = _FakeClassifierModel(response="APPROACH: Build the server.\nSTEPS:\nnone really\n")
        self.assertIsNone(_draft_via_model(_RUST_HTTP_REQUEST, intent, fake))

    def test_empty_response_falls_back(self):
        intent = extract_intent(_RUST_HTTP_REQUEST)
        fake = _FakeClassifierModel(response="")
        self.assertIsNone(_draft_via_model(_RUST_HTTP_REQUEST, intent, fake))

    def test_exception_falls_back(self):
        intent = extract_intent(_RUST_HTTP_REQUEST)
        fake = _FakeClassifierModel(raise_exc=RuntimeError("boom"))
        self.assertIsNone(_draft_via_model(_RUST_HTTP_REQUEST, intent, fake))

    def test_timeout_falls_back(self):
        os.environ["Z_PLAN_CLASSIFY_TIMEOUT"] = "0.2"
        intent = extract_intent(_RUST_HTTP_REQUEST)
        fake = _FakeClassifierModel(hang=True)
        self.assertIsNone(_draft_via_model(_RUST_HTTP_REQUEST, intent, fake))

    def test_escape_hatch_skips_the_model_call_entirely(self):
        os.environ["Z_PLAN_CLASSIFY"] = "0"
        intent = extract_intent(_RUST_HTTP_REQUEST)
        fake = _FakeClassifierModel(response=_GOOD_RESPONSE)
        self.assertIsNone(_draft_via_model(_RUST_HTTP_REQUEST, intent, fake))
        self.assertEqual(fake.calls, [])


class DraftPlanFromRequestIntegrationTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_PLAN_CLASSIFY", None)
        os.environ.pop("Z_PLAN_CLASSIFY_TIMEOUT", None)

    def test_baseline_without_classifier_model_uses_template(self):
        plan = draft_plan_from_request(_RUST_HTTP_REQUEST, reason="test")
        # The generic backend template — confirms nothing about Rust/HTTP is
        # in the deterministic path, i.e. this is the bug being fixed.
        self.assertIn("backend endpoints", plan.approach)

    def test_model_overrides_the_template(self):
        fake = _FakeClassifierModel(response=_GOOD_RESPONSE)
        plan = draft_plan_from_request(
            _RUST_HTTP_REQUEST, reason="test", classifier_model=fake
        )
        self.assertIn("Rust", plan.approach)
        self.assertIn("Cargo", plan.steps[0])

    def test_model_failure_falls_back_to_template(self):
        fake = _FakeClassifierModel(raise_exc=RuntimeError("boom"))
        plan = draft_plan_from_request(
            _RUST_HTTP_REQUEST, reason="test", classifier_model=fake
        )
        self.assertIn("backend endpoints", plan.approach)

    def test_non_implement_mode_never_calls_the_model(self):
        intent = extract_intent("investigate why the api fails; do not edit")
        self.assertNotEqual((intent.mode or "").lower(), "implement")
        fake = _FakeClassifierModel(response=_GOOD_RESPONSE)
        plan = draft_plan_from_request(
            "investigate why the api fails; do not edit",
            reason="test",
            intent=intent,
            classifier_model=fake,
        )
        self.assertTrue(plan.skipped)
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
