"""Hardened JSON extraction from LLM responses (shared llm_json utility)."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_llm_json_")
os.environ["Z_HOME"] = _HOME

from aider.z.llm_json import extract_json_from_response  # noqa: E402
from aider.z.skills.generate import _extract_json, _skill_from_data  # noqa: E402


class ExtractJsonTest(unittest.TestCase):
    def test_extract_json_survives_stray_brace_in_preamble(self):
        raw = (
            "Here's the bug pattern skill. Note the original regex used "
            "\\d{1,3} incorrectly:\n\n```json\n"
            '{"title": "x", "root_cause_category": "use_after_free"}\n```\n'
        )
        data = _extract_json(raw)
        self.assertIsNotNone(data)
        self.assertEqual(data["root_cause_category"], "use_after_free")

    def test_extract_json_survives_stray_brace_in_postamble(self):
        raw = '```json\n{"title": "x"}\n```\n\nHope this helps with your {config} setup!'
        data = _extract_json(raw)
        self.assertEqual(data, {"title": "x"})

    def test_extract_json_still_handles_clean_fenced_json(self):
        self.assertEqual(
            _extract_json('```json\n{"title": "x"}\n```'),
            {"title": "x"},
        )

    def test_extract_json_still_handles_bare_json(self):
        self.assertEqual(_extract_json('{"title": "x"}'), {"title": "x"})

    def test_extract_json_preserves_nested_braces_in_string_values(self):
        raw = '{"title": "x", "content": "example: config = {\\"a\\": 1}"}'
        data = _extract_json(raw)
        self.assertEqual(data["content"], 'example: config = {"a": 1}')

    def test_extract_json_returns_none_for_no_json(self):
        self.assertIsNone(
            _extract_json("Sorry, I cannot help with that {request}.")
        )

    def test_shared_utility_matches_generate_wrapper(self):
        raw = (
            "Preamble with \\d{1,3}:\n```json\n"
            '{"title": "shared"}\n```\n'
        )
        self.assertEqual(_extract_json(raw), extract_json_from_response(raw))

    def test_skill_from_data_no_longer_needs_raw_fallback_for_valid_json_with_prose(self):
        raw = (
            "Here's the fix. Original code used \\d{1,3}:\n\n```json\n"
            '{"title": "t", "content": "c", "root_cause_category": "use_after_free"}\n'
            "```\n"
        )
        data = _extract_json(raw)
        skill = _skill_from_data(
            data or {},
            "topic",
            created_by="x",
            source="generate",
            raw_fallback=raw,
        )
        self.assertIsNotNone(skill)
        self.assertEqual(skill.root_cause_category, "use_after_free")
        self.assertEqual(skill.content, "c")


if __name__ == "__main__":
    unittest.main()
