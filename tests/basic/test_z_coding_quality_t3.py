"""Coding quality tranche 3: strict SEARCH, AGENTS.md, live P2 adapter."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_HOME = tempfile.mkdtemp(prefix="z_cq3_")
os.environ["Z_HOME"] = _HOME


class StrictSearchTests(unittest.TestCase):
    def _make_coder(self, root: Path, files: dict):
        from aider.coders import Coder
        from aider.io import InputOutput
        from aider.models import Model

        abs_paths = []
        for rel, body in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            abs_paths.append(str(p.resolve()))

        io = InputOutput(yes=True)
        coder = Coder.create(
            main_model=Model("gpt-4o-mini"),
            io=io,
            fnames=[],
            edit_format="diff",
        )
        coder.root = str(root)
        coder.repo = None
        coder.abs_fnames = set(abs_paths)
        coder.fence = ("```", "```")
        return coder

    def test_strict_blocks_cross_file_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            coder = self._make_coder(
                root,
                {
                    "wrong.py": "def alpha():\n    return 1\n",
                    "right.py": "def beta():\n    return 2\n",
                },
            )
            os.environ["Z_STRICT_SEARCH"] = "1"
            edits = [
                (
                    "wrong.py",
                    "def beta():\n    return 2\n",
                    "def beta():\n    return 3\n",
                )
            ]
            with self.assertRaises(ValueError) as ctx:
                coder.apply_edits(edits)
            self.assertIn("failed to match", str(ctx.exception).lower())
            self.assertEqual(
                (root / "right.py").read_text(encoding="utf-8"),
                "def beta():\n    return 2\n",
            )
            self.assertEqual(
                (root / "wrong.py").read_text(encoding="utf-8"),
                "def alpha():\n    return 1\n",
            )

    def test_legacy_cross_file_when_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            coder = self._make_coder(
                root,
                {
                    "wrong.py": "def alpha():\n    return 1\n",
                    "right.py": "def beta():\n    return 2\n",
                },
            )
            os.environ["Z_STRICT_SEARCH"] = "0"
            try:
                edits = [
                    (
                        "wrong.py",
                        "def beta():\n    return 2\n",
                        "def beta():\n    return 3\n",
                    )
                ]
                coder.apply_edits(edits)
                self.assertEqual(
                    (root / "right.py").read_text(encoding="utf-8"),
                    "def beta():\n    return 3\n",
                )
            finally:
                os.environ.pop("Z_STRICT_SEARCH", None)


class HouseInstructionsTests(unittest.TestCase):
    def test_loads_agents_md(self):
        from aider.z.house_instructions import load_house_instructions

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "AGENTS.md").write_text(
                "# Project rules\nAlways use type hints.\n", encoding="utf-8"
            )
            os.environ["Z_HOUSE_INSTRUCTIONS"] = "1"
            block = load_house_instructions(root)
            self.assertIn("House instructions", block)
            self.assertIn("type hints", block)

    def test_disabled_returns_empty(self):
        from aider.z.house_instructions import load_house_instructions

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "AGENTS.md").write_text("secret rule\n", encoding="utf-8")
            os.environ["Z_HOUSE_INSTRUCTIONS"] = "0"
            try:
                self.assertEqual(load_house_instructions(root), "")
            finally:
                os.environ.pop("Z_HOUSE_INSTRUCTIONS", None)

    def test_inject_once_per_session(self):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput
        from aider.models import Model

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "AGENTS.md").write_text("Prefer pytest.\n", encoding="utf-8")
            io = InputOutput(yes=True)
            coder = Coder.create(
                main_model=Model("gpt-4o-mini"),
                io=io,
                fnames=[],
                edit_format="diff",
            )
            coder.root = str(root)
            coder.repo = None
            coder.cur_messages = []
            os.environ["Z_HOUSE_INSTRUCTIONS"] = "1"
            coder._maybe_inject_house_instructions()
            n1 = len(coder.cur_messages)
            self.assertGreater(n1, 0)
            self.assertTrue(coder._house_instructions_injected)
            coder._maybe_inject_house_instructions()
            self.assertEqual(len(coder.cur_messages), n1)


class LiveAdapterTests(unittest.TestCase):
    def test_select_default_scripted(self):
        from aider.z.benchmark.agent import ScriptedAgentAdapter
        from aider.z.benchmark.live_adapter import select_adapter

        os.environ.pop("Z_P2_ADAPTER", None)
        self.assertIsInstance(select_adapter(None), ScriptedAgentAdapter)
        self.assertIsInstance(select_adapter("scripted"), ScriptedAgentAdapter)

    def test_live_disabled_stub(self):
        from aider.z.benchmark.issues import BenchmarkIssue
        from aider.z.benchmark.live_adapter import LiveAgentAdapter, select_adapter

        os.environ.pop("Z_P2_LIVE", None)
        adapter = select_adapter("live")
        self.assertIsInstance(adapter, LiveAgentAdapter)

        issue = MagicMock(spec=BenchmarkIssue)
        issue.id = "p2-test"
        issue.task_prompt = "fix it"
        issue.timeout_s = 5
        with tempfile.TemporaryDirectory() as td:
            trace = adapter.run(issue, Path(td), uncertainty_enabled=True)
        self.assertTrue(trace.timed_out)
        self.assertIn("live_disabled", trace.pipeline)
        self.assertFalse(trace.self_reported_complete)
