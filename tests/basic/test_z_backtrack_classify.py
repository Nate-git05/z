"""Weak-model override of causal backtracking's earliest-node pick.

backtrack_failure() only ever escalates to a model when the deterministic
chain-walk found more than one candidate node AND a classifier_model is
supplied — the default (None) keeps it pure rule-based, unchanged from
before this override existed. These tests use a fake model so nothing here
needs network access or a real API key.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

_HOME = tempfile.mkdtemp(prefix="z_backtrack_clf_")
os.environ["Z_HOME"] = _HOME
os.environ.pop("Z_BACKTRACK_CLASSIFY", None)
os.environ.pop("Z_BACKTRACK_CLASSIFY_TIMEOUT", None)

from aider.z.uncertainty.backtrack import backtrack_failure  # noqa: E402

# A type_error failure with no ledger walks all the way to env_ready,
# visiting types_match -> deps_installed -> env_ready along the way — three
# real candidates for the model to choose between.
_TYPE_ERROR_KWARGS = dict(
    output="error TS2339: property missing", failure_kind="typecheck"
)


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


class BacktrackClassifyTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_BACKTRACK_CLASSIFY", None)
        os.environ.pop("Z_BACKTRACK_CLASSIFY_TIMEOUT", None)

    def test_baseline_without_classifier_model_is_rule_selected(self):
        bt = backtrack_failure(**_TYPE_ERROR_KWARGS)
        self.assertEqual(bt.earliest_selected_by, "rule")
        self.assertEqual(bt.earliest.id, "env_ready")

    def test_model_overrides_with_a_valid_different_id(self):
        fake = _FakeClassifierModel(response="deps_installed")
        bt = backtrack_failure(classifier_model=fake, **_TYPE_ERROR_KWARGS)
        self.assertEqual(bt.earliest.id, "deps_installed")
        self.assertEqual(bt.earliest_selected_by, "model")
        self.assertEqual(bt.earliest.status, "contradicted")
        self.assertEqual(len(fake.calls), 1)

    def test_invalid_id_falls_back_to_rule_pick(self):
        fake = _FakeClassifierModel(response="journey_works")
        bt = backtrack_failure(classifier_model=fake, **_TYPE_ERROR_KWARGS)
        self.assertEqual(bt.earliest.id, "env_ready")
        self.assertEqual(bt.earliest_selected_by, "rule")

    def test_unknown_word_falls_back_to_rule_pick(self):
        fake = _FakeClassifierModel(response="not_a_real_node")
        bt = backtrack_failure(classifier_model=fake, **_TYPE_ERROR_KWARGS)
        self.assertEqual(bt.earliest.id, "env_ready")
        self.assertEqual(bt.earliest_selected_by, "rule")

    def test_timeout_falls_back_safely(self):
        os.environ["Z_BACKTRACK_CLASSIFY_TIMEOUT"] = "0.2"
        fake = _FakeClassifierModel(hang=True)
        bt = backtrack_failure(classifier_model=fake, **_TYPE_ERROR_KWARGS)
        self.assertEqual(bt.earliest_selected_by, "rule")

    def test_exception_falls_back_safely(self):
        fake = _FakeClassifierModel(raise_exc=RuntimeError("boom"))
        bt = backtrack_failure(classifier_model=fake, **_TYPE_ERROR_KWARGS)
        self.assertEqual(bt.earliest_selected_by, "rule")

    def test_empty_response_falls_back_safely(self):
        fake = _FakeClassifierModel(response="")
        bt = backtrack_failure(classifier_model=fake, **_TYPE_ERROR_KWARGS)
        self.assertEqual(bt.earliest_selected_by, "rule")

    def test_escape_hatch_skips_the_model_call_entirely(self):
        os.environ["Z_BACKTRACK_CLASSIFY"] = "0"
        fake = _FakeClassifierModel(response="deps_installed")
        bt = backtrack_failure(classifier_model=fake, **_TYPE_ERROR_KWARGS)
        self.assertEqual(bt.earliest_selected_by, "rule")
        self.assertEqual(fake.calls, [])

    def test_single_candidate_never_calls_the_model(self):
        # command_not_found's target (env_ready) has no parent, so the walk
        # never leaves it — only one candidate, nothing to choose between.
        fake = _FakeClassifierModel(response="deps_installed")
        bt = backtrack_failure(
            output="sh: tsc: command not found",
            command="npm run typecheck",
            exit_code=127,
            failure_kind="typecheck",
            classifier_model=fake,
        )
        self.assertEqual(bt.earliest.id, "env_ready")
        self.assertEqual(bt.earliest_selected_by, "rule")
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
