"""infer_phase_kind — mapping raw phase strings to the small kind vocabulary."""

from __future__ import annotations

import unittest


class InferPhaseKindTests(unittest.TestCase):
    def test_all_real_call_site_strings(self):
        from aider.z.phase_kinds import (
            EXPLORING,
            PLANNING,
            THINKING,
            WAITING_MODEL,
            infer_phase_kind,
        )

        cases = {
            "Planning — matching skills…": THINKING,
            "Planning — routing skills…": THINKING,
            "Planning — refining plan interview…": THINKING,
            "Planning — exploring related files…": EXPLORING,
            "Planning — drafting approach checklist…": PLANNING,
            "Planning — building capability plan…": PLANNING,
            "Planning — enriching approach steps…": PLANNING,
            "Planning — scoring blast radius…": PLANNING,
            "Planning — drafting implementation plan…": PLANNING,
            "Waiting for claude-sonnet-5": WAITING_MODEL,
            "Waiting for gpt-4o": WAITING_MODEL,
        }
        for text, expected in cases.items():
            self.assertEqual(infer_phase_kind(text), expected, msg=text)

    def test_empty_and_none_default_to_thinking(self):
        from aider.z.phase_kinds import DEFAULT_KIND, infer_phase_kind

        self.assertEqual(infer_phase_kind(""), DEFAULT_KIND)
        self.assertEqual(infer_phase_kind(None), DEFAULT_KIND)

    def test_case_insensitive(self):
        from aider.z.phase_kinds import EXPLORING, infer_phase_kind

        self.assertEqual(infer_phase_kind("PLANNING — EXPLORING FILES…"), EXPLORING)

    def test_substring_collision_guard(self):
        """"latest changes" must not false-positive as EDITING/VERIFYING —
        these kinds are reserved for real "editing"/"verifying" text, not
        bare "edit"/"test" substrings that occur inside unrelated words."""
        from aider.z.phase_kinds import EDITING, VERIFYING, infer_phase_kind

        self.assertNotEqual(infer_phase_kind("Applying the latest changes…"), EDITING)
        self.assertNotEqual(infer_phase_kind("Applying the latest changes…"), VERIFYING)

    def test_editing_and_verifying_kinds(self):
        from aider.z.phase_kinds import EDITING, VERIFYING, infer_phase_kind

        self.assertEqual(infer_phase_kind("Editing the files…"), EDITING)
        self.assertEqual(infer_phase_kind("Verifying the changes…"), VERIFYING)


if __name__ == "__main__":
    unittest.main()
