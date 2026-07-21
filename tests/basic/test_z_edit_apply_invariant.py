"""Eval Finding 2: SEARCH/REPLACE that never lands must fail loudly."""

from __future__ import annotations

import unittest

from aider.coders.base_coder import Coder


class EditApplyInvariantTest(unittest.TestCase):
    def test_detects_search_replace_fences(self):
        text = (
            "Here is the fix:\n"
            "foo.hpp\n"
            "<<<<<<< SEARCH\n"
            "=======\n"
            "int x = 1;\n"
            ">>>>>>> REPLACE\n"
        )
        self.assertTrue(Coder._response_has_edit_blocks(text))
        self.assertFalse(Coder._response_has_edit_blocks("no edits here"))
        self.assertFalse(
            Coder._response_has_edit_blocks("<<<<<<< SEARCH\nno divider or replace")
        )


if __name__ == "__main__":
    unittest.main()
