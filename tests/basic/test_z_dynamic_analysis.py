"""Dynamic-risk taxonomy — concurrency / memory_safety / leaks."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_dynamic_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.dynamic_analysis import (  # noqa: E402
    DynamicComparison,
    SanitizerRunResult,
    analyze_category,
    category_by_id,
    classify_outcome,
    nodes_from_comparison,
    parse_issue_count,
    tag_category,
    taxonomy_category_ids,
)
from aider.z.uncertainty.gate import (  # noqa: E402
    _effective_gate_tier,
    classify_nodes,
)
from aider.z.uncertainty.risk import DetectionSignals  # noqa: E402
from aider.z.uncertainty.schema import NodeType, Tier  # noqa: E402


_MALLOC_MEMCPY_DIFF = (
    "diff --git a/src/buf.cpp b/src/buf.cpp\n"
    "--- a/src/buf.cpp\n"
    "+++ b/src/buf.cpp\n"
    "@@ -1,2 +1,6 @@\n"
    "+void* p = malloc(n);\n"
    "+memcpy(p, src, n);\n"
)

_NEW_ALLOC_DIFF = (
    "diff --git a/src/pool.cpp b/src/pool.cpp\n"
    "--- a/src/pool.cpp\n"
    "+++ b/src/pool.cpp\n"
    "@@ -1,2 +1,5 @@\n"
    "+auto* p = new Widget();\n"
    "+char* buf = (char*)malloc(64);\n"
)

_PYTHON_UTIL_DIFF = (
    "diff --git a/util.py b/util.py\n"
    "--- a/util.py\n"
    "+++ b/util.py\n"
    "@@ -1,2 +1,3 @@\n"
    "+def add(a, b): return a + b\n"
)


class TaxonomyTest(unittest.TestCase):
    def test_taxonomy_has_three_category_ids(self):
        ids = taxonomy_category_ids()
        self.assertEqual(set(ids), {"concurrency", "memory_safety", "leaks"})
        self.assertEqual(len(ids), 3)


class MemorySafetyTaggingTest(unittest.TestCase):
    def test_tags_malloc_memcpy_on_cpp(self):
        cat = category_by_id("memory_safety")
        assert cat is not None
        tag = tag_category(cat, _MALLOC_MEMCPY_DIFF, ["src/buf.cpp"])
        self.assertTrue(tag.relevant)
        blob = " ".join(tag.reasons).lower()
        self.assertTrue(
            "malloc" in blob or "memcpy" in blob,
            tag.reasons,
        )

    def test_pure_python_util_not_tagged(self):
        cat = category_by_id("memory_safety")
        assert cat is not None
        tag = tag_category(cat, _PYTHON_UTIL_DIFF, ["util.py"])
        self.assertFalse(tag.relevant)


class LeakTaggingTest(unittest.TestCase):
    def test_tags_malloc_new_diffs(self):
        cat = category_by_id("leaks")
        assert cat is not None
        tag = tag_category(cat, _NEW_ALLOC_DIFF, ["src/pool.cpp"])
        self.assertTrue(tag.relevant)
        blob = " ".join(tag.reasons).lower()
        self.assertTrue(
            "malloc" in blob or "new" in blob,
            tag.reasons,
        )


class ParseIssueCountTest(unittest.TestCase):
    def test_parse_asan(self):
        cat = category_by_id("memory_safety")
        assert cat is not None
        out = (
            "ERROR: AddressSanitizer: heap-buffer-overflow on address 0x1\n"
            "SUMMARY: AddressSanitizer: heap-buffer-overflow buf.cpp:12\n"
        )
        self.assertGreaterEqual(parse_issue_count(out, cat), 1)

    def test_parse_lsan(self):
        cat = category_by_id("leaks")
        assert cat is not None
        out = (
            "ERROR: LeakSanitizer: detected memory leaks\n"
            "Direct leak of 32 byte(s) in 1 object(s)\n"
            "    definitely lost: 32 bytes in 1 blocks\n"
        )
        self.assertGreaterEqual(parse_issue_count(out, cat), 1)


class SoftBlockAndOutcomeTest(unittest.TestCase):
    def test_tool_missing_soft_block_memory_safety(self):
        cat = category_by_id("memory_safety")
        assert cat is not None
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "buf.cpp").write_text(
                "void* p = malloc(8);\n", encoding="utf-8"
            )
            for key in ("Z_ASAN_CMD", "Z_MEMORY_DETECT_CMD"):
                os.environ.pop(key, None)
            cmp_ = analyze_category(
                root,
                cat,
                diff=_MALLOC_MEMCPY_DIFF,
                edited=["src/buf.cpp"],
            )
            self.assertTrue(cmp_.relevant)
            self.assertEqual(cmp_.outcome, "tool_missing")
            self.assertTrue(cmp_.soft_block)
            self.assertFalse(cmp_.blocks_commit)

            sig = DetectionSignals(files_changed=["src/buf.cpp"])
            nodes = nodes_from_comparison(
                cmp_, signals=sig, files=["src/buf.cpp"]
            )
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].type, NodeType.MEMORY_SAFETY)
            self.assertEqual(nodes[0].risk_tier, Tier.MEDIUM)
            self.assertEqual(_effective_gate_tier(nodes[0]), Tier.MEDIUM)
            high, medium = classify_nodes(nodes)
            self.assertEqual(len(high), 0)
            self.assertEqual(len(medium), 1)

    def test_classify_outcome_reduction(self):
        before = SanitizerRunResult(ran=True, issue_count=5, phase="before")
        after = SanitizerRunResult(ran=True, issue_count=2, phase="after")
        outcome, summary = classify_outcome(before, after, issue_noun="memory error")
        self.assertEqual(outcome, "reduced")
        self.assertIn("5", summary)
        self.assertIn("2", summary)
        self.assertTrue(DynamicComparison(outcome="reduced").soft_block)
        self.assertFalse(DynamicComparison(outcome="reduced").blocks_commit)


if __name__ == "__main__":
    unittest.main()
