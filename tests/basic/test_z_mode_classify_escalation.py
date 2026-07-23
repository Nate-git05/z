"""Weak-model escalation for TaskMode's ambiguous fallback zone.

classify_task_mode()/extract_intent() only ever escalate to a model when
every regex heuristic fails to place the message AND a classifier_model is
supplied — the default (None) keeps them pure-regex, unchanged from before
this escalation existed. These tests use a fake model so nothing here needs
network access or a real API key.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

_HOME = tempfile.mkdtemp(prefix="z_mode_clf_esc_")
os.environ["Z_HOME"] = _HOME
os.environ.pop("Z_MODE_CLASSIFY", None)
os.environ.pop("Z_MODE_CLASSIFY_TIMEOUT", None)

from aider.z.task_mode import TaskMode, classify_task_mode  # noqa: E402
from aider.z.uncertainty.intent import extract_intent  # noqa: E402

# Reaches today's bottom-of-function IMPLEMENT fallback: not a bare greeting,
# not an ambiguous topic, not a question, no investigate/review/verify verb.
_AMBIGUOUS_TEXT = "I'm working on a Python project."


class _FakeClassifierModel:
    def __init__(self, response=None, *, raise_exc=None, hang=False):
        self.response = response
        self.raise_exc = raise_exc
        self.hang = hang
        self.calls = []

    def simple_send_with_retries(self, messages):
        self.calls.append(messages)
        if self.hang:
            # Short, not the configured timeout's actual worst case: this
            # fake runs on aider/z/latency.py's SHARED 2-worker pool, and a
            # long real sleep here would starve other tests' legitimate
            # (fast) submit_background calls if they land in the same
            # pytest process while this thread is still occupying a worker.
            time.sleep(2)
        if self.raise_exc:
            raise self.raise_exc
        return self.response


class ModeEscalationTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_MODE_CLASSIFY", None)
        os.environ.pop("Z_MODE_CLASSIFY_TIMEOUT", None)

    def test_baseline_without_classifier_model_is_unchanged(self):
        self.assertEqual(
            classify_task_mode(None, _AMBIGUOUS_TEXT), TaskMode.IMPLEMENT
        )

    def test_model_overrides_the_default(self):
        fake = _FakeClassifierModel(response="ask")
        got = classify_task_mode(
            None, _AMBIGUOUS_TEXT, classifier_model=fake
        )
        self.assertEqual(got, TaskMode.ASK)
        self.assertEqual(len(fake.calls), 1)

    def test_timeout_falls_back_safely(self):
        os.environ["Z_MODE_CLASSIFY_TIMEOUT"] = "0.2"
        fake = _FakeClassifierModel(hang=True)
        got = classify_task_mode(
            None, _AMBIGUOUS_TEXT, classifier_model=fake
        )
        self.assertEqual(got, TaskMode.IMPLEMENT)

    def test_exception_falls_back_safely(self):
        fake = _FakeClassifierModel(raise_exc=RuntimeError("boom"))
        got = classify_task_mode(
            None, _AMBIGUOUS_TEXT, classifier_model=fake
        )
        self.assertEqual(got, TaskMode.IMPLEMENT)

    def test_garbage_output_falls_back_safely(self):
        fake = _FakeClassifierModel(response="not a real mode word at all")
        got = classify_task_mode(
            None, _AMBIGUOUS_TEXT, classifier_model=fake
        )
        self.assertEqual(got, TaskMode.IMPLEMENT)

    def test_escape_hatch_skips_the_model_call_entirely(self):
        os.environ["Z_MODE_CLASSIFY"] = "0"
        fake = _FakeClassifierModel(response="ask")
        got = classify_task_mode(
            None, _AMBIGUOUS_TEXT, classifier_model=fake
        )
        self.assertEqual(got, TaskMode.IMPLEMENT)
        self.assertEqual(fake.calls, [])

    def test_extract_intent_wiring(self):
        fake = _FakeClassifierModel(response="ask")
        intent = extract_intent(_AMBIGUOUS_TEXT, classifier_model=fake)
        self.assertEqual(intent.mode, "ask")
        self.assertEqual(intent.requested_actions, [])


if __name__ == "__main__":
    unittest.main()
