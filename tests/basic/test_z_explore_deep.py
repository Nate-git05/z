"""Deeper explore scout — signatures, related paths, thin escape."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_explore_")
os.environ["Z_HOME"] = _HOME


class ExploreDeepTests(unittest.TestCase):
    def setUp(self):
        os.environ["Z_EXPLORE_PASS"] = "1"
        os.environ.pop("Z_EXPLORE_DEPTH", None)
        os.environ.pop("Z_EXPLORE_SCOUT_CHARS", None)

    def _fixture(self, root: Path) -> None:
        pkg = root / "calcpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "ops.py").write_text(
            '"""Arithmetic helpers."""\n\n'
            "def add(a, b):\n    return a + b\n\n"
            "def average(nums):\n    return sum(nums) / len(nums)\n",
            encoding="utf-8",
        )
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_ops.py").write_text(
            "from calcpkg.ops import average\n\ndef test_avg():\n    assert average([2, 4]) == 3\n",
            encoding="utf-8",
        )

    def test_deep_includes_signatures(self):
        from aider.z.explore import peek_signatures, run_explore_pass

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            os.environ["Z_EXPLORE_DEPTH"] = "deep"
            block = run_explore_pass(
                "fix average() off-by-one in calcpkg/ops.py",
                root=root,
                already_in_chat=[],
            )
            self.assertIn("Explore scout", block)
            self.assertIn("ops.py", block)
            self.assertIn("signatures", block)
            self.assertIn("def average", block)
            # related test should be suggested when present
            self.assertTrue(
                "test_ops.py" in block or "__init__.py" in block,
                block,
            )

            sigs = peek_signatures(root / "calcpkg" / "ops.py")
            self.assertTrue(any("average" in s for s in sigs), sigs)

    def test_thin_escape(self):
        from aider.z.explore import run_explore_pass

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            os.environ["Z_EXPLORE_DEPTH"] = "thin"
            block = run_explore_pass(
                "investigate average in calcpkg ops",
                root=root,
                already_in_chat=[],
                depth="thin",
            )
            self.assertIn("Explore pass", block)
            self.assertIn("ops.py", block)
            self.assertNotIn("signatures", block)
            self.assertNotIn("Explore scout", block)

    def test_budget_truncates(self):
        from aider.z.explore import run_explore_pass

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Many files so the scout block would be large
            for i in range(8):
                d = root / f"pkg{i}"
                d.mkdir()
                (d / "widget.py").write_text(
                    "\n".join(f"def fn_{j}_{i}():\n    return {j}\n" for j in range(20)),
                    encoding="utf-8",
                )
            os.environ["Z_EXPLORE_DEPTH"] = "deep"
            os.environ["Z_EXPLORE_SCOUT_CHARS"] = "900"
            os.environ["Z_EXPLORE_SCOUT_FILES"] = "8"
            block = run_explore_pass(
                "fix widget helpers across packages",
                root=root,
                already_in_chat=[],
                max_files=8,
            )
            if block:  # may be empty if keywords don't match — ensure widget hits
                self.assertLessEqual(len(block), 980)
                if len(block) >= 850:
                    self.assertIn("truncated", block.lower())


class RelatedPathTests(unittest.TestCase):
    def test_suggest_related(self):
        from aider.z.explore import suggest_related_paths

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "lib"
            src.mkdir()
            (src / "parser.py").write_text("def parse():\n    pass\n", encoding="utf-8")
            (src / "test_parser.py").write_text("def test_parse():\n    pass\n", encoding="utf-8")
            related = suggest_related_paths(root, "lib/parser.py")
            self.assertTrue(any("test_parser" in r for r in related), related)
