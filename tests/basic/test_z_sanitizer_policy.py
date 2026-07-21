"""Sanitizer policy: tool_missing hard vs soft + recipe extraction."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HOME = tempfile.mkdtemp(prefix="z_san_pol_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.dynamic_analysis import (  # noqa: E402
    DynamicComparison,
    analyze_category,
    category_by_id,
    nodes_from_comparison,
    sanitizer_policy,
    sanitizer_policy_is_hard,
)
from aider.z.uncertainty.gate import classify_nodes, _effective_gate_tier  # noqa: E402
from aider.z.uncertainty.recipe_runner import (  # noqa: E402
    extract_sanitizer_recipes,
    try_run_sanitizer_recipes,
)
from aider.z.uncertainty.risk import DetectionSignals  # noqa: E402
from aider.z.uncertainty.schema import NodeType, Tier  # noqa: E402

_MALLOC_DIFF = (
    "diff --git a/src/buf.cpp b/src/buf.cpp\n"
    "--- a/src/buf.cpp\n"
    "+++ b/src/buf.cpp\n"
    "@@ -1,3 +1,4 @@\n"
    "+    void* p = malloc(8);\n"
    "+    memcpy(p, src, n);\n"
)


class SanitizerPolicyEnvTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_SANITIZER_POLICY", None)

    def test_default_soft(self):
        os.environ.pop("Z_SANITIZER_POLICY", None)
        self.assertEqual(sanitizer_policy(), "soft")
        self.assertFalse(sanitizer_policy_is_hard())

    def test_env_hard(self):
        os.environ["Z_SANITIZER_POLICY"] = "hard"
        self.assertEqual(sanitizer_policy(), "hard")
        self.assertTrue(sanitizer_policy_is_hard())

    def test_non_interactive_defaults_hard(self):
        os.environ.pop("Z_SANITIZER_POLICY", None)
        self.assertEqual(sanitizer_policy(non_interactive=True), "hard")
        self.assertEqual(sanitizer_policy(non_interactive=False), "soft")


class BlocksCommitPolicyTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_SANITIZER_POLICY", None)

    def test_tool_missing_soft_by_default(self):
        os.environ.pop("Z_SANITIZER_POLICY", None)
        cmp_ = DynamicComparison(outcome="tool_missing")
        self.assertTrue(cmp_.soft_block)
        self.assertFalse(cmp_.blocks_commit)

    def test_tool_missing_hard_via_flag(self):
        cmp_ = DynamicComparison(outcome="tool_missing", hard_policy=True)
        self.assertTrue(cmp_.blocks_commit)
        self.assertFalse(cmp_.soft_block)

    def test_tool_missing_hard_via_env(self):
        os.environ["Z_SANITIZER_POLICY"] = "hard"
        cmp_ = DynamicComparison(outcome="tool_missing")
        self.assertTrue(cmp_.blocks_commit)

    def test_regression_still_hard(self):
        cmp_ = DynamicComparison(outcome="regression")
        self.assertTrue(cmp_.blocks_commit)


class HardPolicyAnalyzeTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_SANITIZER_POLICY", None)
        for key in ("Z_ASAN_CMD", "Z_MEMORY_DETECT_CMD"):
            os.environ.pop(key, None)

    def test_ni_tool_missing_blocks_and_high_node(self):
        cat = category_by_id("memory_safety")
        assert cat is not None
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "buf.cpp").write_text(
                "void* p = malloc(8);\n", encoding="utf-8"
            )
            cmp_ = analyze_category(
                root,
                cat,
                diff=_MALLOC_DIFF,
                edited=["src/buf.cpp"],
                non_interactive=True,
            )
            self.assertEqual(cmp_.outcome, "tool_missing")
            self.assertTrue(cmp_.hard_policy)
            self.assertTrue(cmp_.blocks_commit)
            self.assertFalse(cmp_.soft_block)

            nodes = nodes_from_comparison(
                cmp_,
                signals=DetectionSignals(files_changed=["src/buf.cpp"]),
                files=["src/buf.cpp"],
            )
            self.assertEqual(nodes[0].risk_tier, Tier.HIGH)
            self.assertEqual(nodes[0].type, NodeType.MEMORY_SAFETY)
            high, medium = classify_nodes(nodes)
            self.assertEqual(len(high), 1)
            self.assertEqual(len(medium), 0)
            self.assertEqual(_effective_gate_tier(nodes[0]), Tier.HIGH)


class RecipeRunnerTest(unittest.TestCase):
    def test_extract_cmake_asan_and_ctest(self):
        text = """
## Sanitizers

```
cmake -S . -B build-asan -DMINILFU_ASAN=ON
ctest --test-dir build-asan
```

Also: make asan
"""
        recipes = extract_sanitizer_recipes(text)
        self.assertTrue(any("MINILFU_ASAN=ON" in r for r in recipes))
        self.assertTrue(any("build-asan" in r for r in recipes))
        self.assertTrue(any(r.strip().startswith("make") for r in recipes))

    def test_try_run_records_attempts(self):
        calls = []

        def fake_run(cmd, verbose=False, error_print=None, cwd=None):
            calls.append(cmd)
            return 1, "clang: command not found"

        rr = try_run_sanitizer_recipes(
            Path("."),
            ["cmake -S . -B build-asan -DASAN=ON", "ctest --test-dir build-asan"],
            run_cmd_fn=fake_run,
        )
        self.assertEqual(len(rr.attempted), 2)
        self.assertFalse(rr.ran_ok)
        self.assertEqual(len(calls), 2)

    def test_analyze_records_attempted_recipes_on_miss(self):
        cat = category_by_id("memory_safety")
        assert cat is not None
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "buf.cpp").write_text(
                "void* p = malloc(8);\n", encoding="utf-8"
            )
            (root / "README.md").write_text(
                "cmake -S . -B build-asan -DFOO_ASAN=ON\n",
                encoding="utf-8",
            )
            for key in ("Z_ASAN_CMD", "Z_MEMORY_DETECT_CMD"):
                os.environ.pop(key, None)

            def fake_run(cmd, verbose=False, error_print=None, cwd=None):
                return 1, "missing toolchain"

            with patch(
                "aider.z.uncertainty.recipe_runner.try_run_sanitizer_recipes"
            ) as tr:
                from aider.z.uncertainty.recipe_runner import RecipeRunResult

                tr.return_value = RecipeRunResult(
                    attempted=["cmake -S . -B build-asan -DFOO_ASAN=ON"],
                    ran_ok=False,
                    last_exit_code=1,
                    last_command="cmake -S . -B build-asan -DFOO_ASAN=ON",
                )
                cmp_ = analyze_category(
                    root,
                    cat,
                    diff=_MALLOC_DIFF,
                    edited=["src/buf.cpp"],
                    non_interactive=True,
                )
            self.assertEqual(cmp_.outcome, "tool_missing")
            self.assertTrue(
                any("ASAN" in c for c in cmp_.attempted_commands),
                cmp_.attempted_commands,
            )
            self.assertIn("Attempted recipe", cmp_.summary)


if __name__ == "__main__":
    unittest.main()
