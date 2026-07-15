"""Tests for skill grounding pack, check, needs_review gate, and stale skip."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_skill_ground_")
os.environ["Z_HOME"] = _HOME

from aider.z.skills.grounding import (  # noqa: E402
    FileEvidence,
    GroundingPack,
    build_grounding_pack,
    check_grounding,
    extract_claimed_symbols,
    extract_symbols_from_source,
    format_grounding_pack,
    make_ungrounded_skill_node,
    symbols_still_present,
)
from aider.z.skills.router import collect_repo_signals, route_skill  # noqa: E402
from aider.z.skills.schema import Skill  # noqa: E402
from aider.z.skills.store import LocalSkillStore, skill_from_markdown, skill_to_markdown  # noqa: E402


RATE_LIMITER_SRC = '''\
"""Sliding-window rate limiter used by the API gateway."""

from collections import deque
import time


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: deque = deque()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        self._evict(now)
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True

    def _evict(self, now: float) -> None:
        while self._hits and now - self._hits[0] > self.window_seconds:
            self._hits.popleft()
'''


class SymbolExtractTest(unittest.TestCase):
    def test_extracts_python_class_and_methods(self):
        names = extract_symbols_from_source("limiter.py", RATE_LIMITER_SRC)
        self.assertIn("SlidingWindowRateLimiter", names)
        self.assertIn("allow", names)

    def test_claimed_symbols_from_markdown(self):
        text = (
            "Use `SlidingWindowRateLimiter.allow` — never invent a `TokenBucket`.\n"
            "## Steps\n"
            "1. Call allow()\n"
        )
        claimed = extract_claimed_symbols(text)
        self.assertIn("SlidingWindowRateLimiter", claimed)
        self.assertIn("TokenBucket", claimed)


class GroundingPackTest(unittest.TestCase):
    def test_build_pack_reads_files_and_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "limiter.py"
            path.write_text(RATE_LIMITER_SRC, encoding="utf-8")
            pack = build_grounding_pack(
                user_request="Add a rate limiter",
                files_changed=["limiter.py"],
                root=root,
                diff="+ class SlidingWindowRateLimiter\n",
            )
            self.assertEqual(pack.source_files, ["limiter.py"])
            self.assertIn("SlidingWindowRateLimiter", pack.symbols)
            text = format_grounding_pack(pack)
            self.assertIn("SlidingWindowRateLimiter", text)
            self.assertIn("## Git diff", text)
            self.assertTrue(pack.content_hash())


class GroundingCheckTest(unittest.TestCase):
    def _pack(self) -> GroundingPack:
        return GroundingPack(
            user_request="rate limit API",
            files=[
                FileEvidence(
                    path="limiter.py",
                    content=RATE_LIMITER_SRC,
                    symbols=["SlidingWindowRateLimiter", "allow", "_evict"],
                )
            ],
            symbols=["SlidingWindowRateLimiter", "allow", "_evict"],
        )

    def test_passes_when_skill_uses_real_symbols(self):
        skill = (
            "Use `SlidingWindowRateLimiter` and call `allow` per request key.\n"
            "Do not buffer forever — `_evict` drops old hits.\n"
        )
        result = check_grounding(skill, self._pack())
        self.assertTrue(result.ok)
        self.assertIn("SlidingWindowRateLimiter", result.grounded_symbols)

    def test_fails_when_skill_invents_token_bucket(self):
        skill = (
            "Implement a `TokenBucket` rate limiter with `refill` and `consume`.\n"
            "Store tokens in Redis.\n"
        )
        result = check_grounding(skill, self._pack())
        self.assertFalse(result.ok)
        self.assertIn("TokenBucket", result.missing_symbols)

    def test_ungrounded_node_marks_needs_human_review(self):
        node = make_ungrounded_skill_node(
            skill_title="Rate limiting",
            missing_symbols=["TokenBucket"],
            source_files=["limiter.py"],
            reason="invented TokenBucket",
        )
        self.assertEqual(node.signals.get("skill_grounding"), True)
        self.assertIn("TokenBucket", node.symbols_affected)


class NeedsReviewRouterTest(unittest.TestCase):
    def test_router_skips_needs_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "limiter.py").write_text(RATE_LIMITER_SRC, encoding="utf-8")
            sig = collect_repo_signals(root)
            skill = Skill(
                title="Rate limiting",
                description="How this API rate-limits",
                content="Use SlidingWindowRateLimiter",
                languages=["python"],
                needs_review=True,
                grounded_symbols=["SlidingWindowRateLimiter"],
                source_files=["limiter.py"],
            )
            decision = route_skill(skill, "add rate limiting", sig, score=0.9)
            self.assertFalse(decision.apply)
            self.assertIn("needs review", decision.reason)

    def test_router_skips_stale_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "limiter.py").write_text("# empty now\n", encoding="utf-8")
            sig = collect_repo_signals(root)
            skill = Skill(
                title="Rate limiting",
                description="How this API rate-limits",
                content="Use SlidingWindowRateLimiter",
                languages=["python"],
                needs_review=False,
                grounded_symbols=["SlidingWindowRateLimiter", "allow"],
                source_files=["limiter.py"],
            )
            decision = route_skill(skill, "fix rate limiting", sig, score=0.9)
            self.assertFalse(decision.apply)
            self.assertIn("stale", decision.reason)

    def test_symbols_still_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "limiter.py").write_text(RATE_LIMITER_SRC, encoding="utf-8")
            present, missing = symbols_still_present(
                ["SlidingWindowRateLimiter", "TokenBucket"],
                root=root,
                source_files=["limiter.py"],
            )
            self.assertIn("SlidingWindowRateLimiter", present)
            self.assertIn("TokenBucket", missing)


class SchemaRoundtripGroundingTest(unittest.TestCase):
    def test_frontmatter_preserves_grounding_fields(self):
        skill = Skill(
            title="Rate limiting",
            description="Sliding window limiter",
            content="Use SlidingWindowRateLimiter.allow",
            capability="rate-limit middleware",
            grounded_symbols=["SlidingWindowRateLimiter", "allow"],
            source_files=["limiter.py"],
            needs_review=True,
            grounded_at="2026-07-15T00:00:00+00:00",
            content_hash="abc123",
            source="capture",
        )
        again = skill_from_markdown(skill_to_markdown(skill))
        self.assertTrue(again.needs_review)
        self.assertEqual(again.grounded_symbols, ["SlidingWindowRateLimiter", "allow"])
        self.assertEqual(again.source_files, ["limiter.py"])
        self.assertEqual(again.capability, "rate-limit middleware")
        self.assertEqual(again.content_hash, "abc123")

    def test_accept_clears_needs_review(self):
        root = Path(tempfile.mkdtemp(prefix="z_accept_"))
        store = LocalSkillStore(root=root)
        skill = Skill(
            title="Rate limiting",
            description="d",
            content="body",
            needs_review=True,
            source="capture",
        )
        store.save(skill)

        class FakeIO:
            def tool_output(self, *a, **k):
                pass

            def tool_error(self, *a, **k):
                pass

            def prompt_ask(self, *a, **k):
                return ""

        from aider.z.skills.cli import accept_skill

        with mock.patch("aider.z.skills.cli.LocalSkillStore", return_value=store), mock.patch(
            "aider.z.skills.cli.upsert_skill_vector"
        ):
            rc = accept_skill(FakeIO(), skill.id)
        self.assertEqual(rc, 0)
        loaded = store.get(skill.id)
        self.assertFalse(loaded.needs_review)
        self.assertEqual(loaded.quality_state, "verified")


if __name__ == "__main__":
    unittest.main()
