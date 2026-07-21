"""Live P2 adapter tests — backends, worktree diff, no-credentials path."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_p2_live_")
os.environ["Z_HOME"] = _HOME


class LiveWorktreeTests(unittest.TestCase):
    def test_snapshot_and_diff(self):
        from aider.z.benchmark.live_worktree import diff_worktree, snapshot_worktree

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = root / "a.py"
            a.write_text("x=1\n", encoding="utf-8")
            before = snapshot_worktree(root)
            self.assertIn("a.py", before)
            a.write_text("x=2\n", encoding="utf-8")
            (root / "b.py").write_text("y=1\n", encoding="utf-8")
            changed = diff_worktree(before, root)
            self.assertEqual(changed, ["a.py", "b.py"])


class LiveAdapterBackendTests(unittest.TestCase):
    def setUp(self):
        os.environ["Z_P2_LIVE"] = "1"
        os.environ.pop("Z_P2_LIVE_HOOK", None)
        os.environ.pop("Z_P2_LIVE_REPLAY", None)
        os.environ.pop("Z_P2_LIVE_BACKEND", None)
        # Ensure builtin path doesn't accidentally think we have keys
        self._saved_keys = {}
        for k in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "AZURE_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
            "GROQ_API_KEY",
        ):
            self._saved_keys[k] = os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved_keys.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ.pop("Z_P2_LIVE", None)
        os.environ.pop("Z_P2_LIVE_BACKEND", None)
        os.environ.pop("Z_P2_LIVE_HOOK", None)

    def test_disabled_stub(self):
        from aider.z.benchmark.live_adapter import LiveAgentAdapter
        from aider.z.benchmark.issues import load_issues

        os.environ["Z_P2_LIVE"] = "0"
        issue = load_issues(ids=["p2-011-bugfix-average"])[0]
        with tempfile.TemporaryDirectory() as td:
            trace = LiveAgentAdapter().run(
                issue, Path(td), uncertainty_enabled=True
            )
        self.assertTrue(trace.timed_out)
        self.assertIn("live_disabled", trace.pipeline)

    def test_builtin_no_credentials(self):
        from aider.z.benchmark.live_adapter import LiveAgentAdapter
        from aider.z.benchmark.issues import load_issues

        os.environ["Z_P2_LIVE_BACKEND"] = "z"
        issue = load_issues(ids=["p2-011-bugfix-average"])[0]
        with tempfile.TemporaryDirectory() as td:
            trace = LiveAgentAdapter().run(
                issue, Path(td), uncertainty_enabled=True
            )
        self.assertIn("live_no_credentials", trace.pipeline)
        self.assertFalse(trace.self_reported_complete)
        self.assertIn(trace.mode, ("implement", "investigate", "plan"))
        self.assertTrue(trace.classified_clauses)

    def test_replay_applies_scripted_edits(self):
        from aider.z.benchmark.harness import run_benchmark_issue
        from aider.z.benchmark.issues import load_issues
        from aider.z.benchmark.live_adapter import LiveAgentAdapter

        os.environ["Z_P2_LIVE_BACKEND"] = "replay"
        issue = load_issues(ids=["p2-011-bugfix-average"])[0]
        result = run_benchmark_issue(
            issue,
            uncertainty_enabled=True,
            adapter=LiveAgentAdapter(),
        )
        self.assertTrue(result.hidden_tests_passed, result.hidden_test_details)
        self.assertTrue(result.actually_complete, result)
        self.assertIn("calcpkg/ops.py", result.edits)

    def test_hook_backend(self):
        from aider.z.benchmark.harness import run_benchmark_issue
        from aider.z.benchmark.issues import load_issues
        from aider.z.benchmark.live_adapter import LiveAgentAdapter

        hook = Path(__file__).resolve().parents[2] / "scripts" / "p2_live_hook_example.py"
        self.assertTrue(hook.is_file(), hook)
        os.environ["Z_P2_LIVE_BACKEND"] = "hook"
        os.environ["Z_P2_LIVE_HOOK"] = str(hook)
        issue = load_issues(ids=["p2-011-bugfix-average"])[0]
        result = run_benchmark_issue(
            issue,
            uncertainty_enabled=True,
            adapter=LiveAgentAdapter(),
        )
        self.assertTrue(result.hidden_tests_passed, result.hidden_test_details)
        self.assertTrue(result.actually_complete)

    def test_select_adapter(self):
        from aider.z.benchmark.agent import ScriptedAgentAdapter
        from aider.z.benchmark.live_adapter import LiveAgentAdapter, select_adapter

        self.assertIsInstance(select_adapter("scripted"), ScriptedAgentAdapter)
        self.assertIsInstance(select_adapter("live"), LiveAgentAdapter)
        self.assertIsInstance(select_adapter("z"), LiveAgentAdapter)


class ResolveBackendTests(unittest.TestCase):
    def test_inference(self):
        from aider.z.benchmark.live_adapter import resolve_live_backend

        os.environ.pop("Z_P2_LIVE_BACKEND", None)
        os.environ.pop("Z_P2_LIVE_HOOK", None)
        self.assertEqual(resolve_live_backend(), "z")
        os.environ["Z_P2_LIVE_HOOK"] = "/tmp/hook.py"
        self.assertEqual(resolve_live_backend(), "hook")
        os.environ["Z_P2_LIVE_BACKEND"] = "replay"
        self.assertEqual(resolve_live_backend(), "replay")
