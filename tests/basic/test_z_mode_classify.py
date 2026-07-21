"""Local mode classifier A1: ambiguous noun phrases → ASK."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_mode_clf_")
os.environ["Z_HOME"] = _HOME
os.environ.pop("Z_MODE_CLASSIFY", None)

from aider.z.task_mode import (  # noqa: E402
    TaskMode,
    classify_task_mode,
    looks_like_ambiguous_topic,
)
from aider.z.uncertainty.intent import extract_intent  # noqa: E402


# A3 table fixture (inline for simplicity; mirrors plan cases)
_CASES = [
    {"text": "users and sessions", "mode": "ask"},
    {"text": "auth middleware", "mode": "ask"},
    {"text": "the checkout flow", "mode": "ask"},
    {"text": "redis cache", "mode": "ask"},
    {"text": "add users and sessions", "mode": "implement"},
    {"text": "fix the session store", "mode": "implement"},
    {"text": "implement users and sessions", "mode": "implement"},
    {"text": "Add a new REST endpoint for users", "mode": "implement"},
    {"text": "What are users and sessions?", "mode": "ask"},
    {"text": "hello", "mode": "ask"},
    {"text": "users and sessions — add JWT refresh", "mode": "implement"},
]


class AmbiguousTopicTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_MODE_CLASSIFY", None)

    def test_users_and_sessions_is_ambiguous(self):
        self.assertTrue(looks_like_ambiguous_topic("users and sessions"))

    def test_single_word_not_ambiguous(self):
        # D10: single-token stays out of ambiguous→ASK
        self.assertFalse(looks_like_ambiguous_topic("redis"))
        self.assertFalse(looks_like_ambiguous_topic("sessions"))

    def test_verb_unlocks_implement(self):
        self.assertFalse(looks_like_ambiguous_topic("add users and sessions"))
        self.assertEqual(
            classify_task_mode(None, "add users and sessions"),
            TaskMode.IMPLEMENT,
        )

    def test_escape_disables_a1(self):
        os.environ["Z_MODE_CLASSIFY"] = "0"
        self.assertFalse(looks_like_ambiguous_topic("users and sessions"))
        self.assertEqual(
            classify_task_mode(None, "users and sessions"),
            TaskMode.IMPLEMENT,
        )

    def test_table_classify_and_intent(self):
        for case in _CASES:
            text = case["text"]
            want = case["mode"]
            with self.subTest(text=text):
                got = classify_task_mode(None, text)
                self.assertEqual(got.value, want, f"classify({text!r})")
                intent = extract_intent(text)
                self.assertEqual(intent.mode, want, f"intent({text!r})")
                if want == "ask" and looks_like_ambiguous_topic(text):
                    self.assertFalse(intent.requested_actions, text)

    def test_forced_implement_keeps_topic_as_implement_intent(self):
        intent = extract_intent("users and sessions", forced_mode="implement")
        self.assertEqual(intent.mode, "implement")


if __name__ == "__main__":
    unittest.main()
