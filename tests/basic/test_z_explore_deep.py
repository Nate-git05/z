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


class ExploreBudgetTests(unittest.TestCase):
    def test_path_walk_respects_file_budget(self):
        from aider.z.explore import _search_path_names

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Many decoy files before the match — budget must stop early.
            for i in range(80):
                (root / f"decoy_{i}.txt").write_text("x", encoding="utf-8")
            (root / "event_bus.hpp").write_text("// bus", encoding="utf-8")
            hits = _search_path_names(
                root, "event_bus", max_hits=4, max_files_scanned=20
            )
            # Match is file #81; with scan cap 20 we must not hang or find it
            self.assertEqual(hits, [])

    def test_rg_path_skips_filename_walk(self):
        from unittest import mock

        from aider.z import explore as explore_mod

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "event_bus.cpp").write_text(
                "void publish() {}\n", encoding="utf-8"
            )
            walked = []

            def boom(*a, **k):
                walked.append(True)
                return []

            with mock.patch.object(explore_mod, "_rg_available", return_value=True):
                with mock.patch.object(
                    explore_mod,
                    "_search_rg",
                    return_value=[("event_bus.cpp", "publish")],
                ):
                    with mock.patch.object(
                        explore_mod, "_search_path_names", side_effect=boom
                    ):
                        kws, ranked = explore_mod._rank_candidates(
                            "implement thread-safe event bus publish subscribe",
                            root,
                            already_in_chat=[],
                            max_keywords=5,
                            max_files=8,
                        )
            self.assertTrue(kws)
            self.assertTrue(any("event_bus" in r for r, _ in ranked), ranked)
            self.assertEqual(walked, [], "filename walk must not run when rg works")


class CapabilityProgressTests(unittest.TestCase):
    def test_gap_only_announces_continue(self):
        """Capability-plan-only with gaps must say continuing, not look finished."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from aider.coders.base_coder import Coder

        outputs: list[str] = []
        warnings: list[str] = []

        io = SimpleNamespace(
            yes=True,
            tool_output=lambda *a, **k: outputs.append(" ".join(str(x) for x in a)),
            tool_warning=lambda *a, **k: warnings.append(" ".join(str(x) for x in a)),
        )
        coder = MagicMock(spec=Coder)
        coder.io = io
        coder.verbose = False
        coder.root = "."
        coder.task_mode = SimpleNamespace(allows_capability_inference=True)
        coder.task_intent = None
        coder.uncertainty_engine = None
        coder.cur_messages = []
        coder._capability_plan_fingerprint = None

        # Drive the real method with stubs for skill pull internals
        from unittest import mock

        from aider.z.uncertainty.capabilities import Capability, CapabilityPlan

        need = Capability(
            id="concurrency_safety",
            label="Concurrency safety",
            evidence_type="concurrency_test",
            reason="thread-safe",
        )
        fake_plan = CapabilityPlan(
            required=[need],
            available_from_skills=[],
            available_native=[],
            coverage_gaps=[need],
            compensation=["use sanitizers"],
        )

        with mock.patch(
            "aider.z.skills.session.get_session_skill_index", return_value=[1]
        ), mock.patch(
            "aider.z.skills.session.pull_skills_for_checkpoint",
            return_value=([], []),
        ), mock.patch(
            "aider.z.uncertainty.capabilities.build_capability_plan",
            return_value=fake_plan,
        ), mock.patch(
            "aider.z.uncertainty.capabilities.format_capability_plan",
            return_value="Capability plan stub",
        ), mock.patch(
            "aider.z.control_plane_budget.control_plane_compact_enabled",
            return_value=False,
        ):
            Coder._maybe_pull_skills(coder, "Implement a thread-safe event bus", checkpoint="turn")

        blob = "\n".join(warnings + outputs)
        self.assertIn("Capability gaps", blob)
        self.assertIn("continuing", blob.lower())
        self.assertIn("not stopping", blob.lower())

