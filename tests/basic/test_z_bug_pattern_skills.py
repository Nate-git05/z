"""Bug-pattern skills: capture gates, taxonomy, grounding, routing, hypotheses."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_bug_pattern_")
os.environ["Z_HOME"] = _HOME

from aider.z.skills.bug_concepts import (  # noqa: E402
    BUG_CONCEPTS,
    boost_for_category,
    category_grounded_in_diff,
    language_note,
    taxonomy_category_ids,
)
from aider.z.skills.grounding import (  # noqa: E402
    GroundingPack,
    check_bug_pattern_grounding,
)
from aider.z.skills.router import (  # noqa: E402
    collect_repo_signals,
    language_compatible,
    route_skill,
    task_is_bugfix_intent,
)
from aider.z.skills.schema import SKILL_KIND_BUG_PATTERN, Skill  # noqa: E402
from aider.z.skills.session import (  # noqa: E402
    format_bug_pattern_hypothesis,
    format_skills_for_context,
)
from aider.z.skills.store import LocalSkillStore, skill_from_markdown  # noqa: E402


_ATOMIC_DIFF = (
    "diff --git a/src/queue.hpp b/src/queue.hpp\n"
    "--- a/src/queue.hpp\n"
    "+++ b/src/queue.hpp\n"
    "@@ -10,7 +10,8 @@\n"
    "-    volatile uint32_t size;\n"
    "+    std::atomic<uint32_t> size;\n"
    "+    size.store(n, std::memory_order_release);\n"
)


class CaptureGateTest(unittest.TestCase):
    def test_suggest_allows_commit_without_meaningful_pass(self):
        """Human-approved commit should reopen capture even if verify was red."""
        from aider.coders.base_coder import Coder

        class FakeIO:
            yes = None

            def confirm_ask(self, *a, **k):
                return False

            def tool_output(self, *a, **k):
                pass

        coder = Coder.__new__(Coder)
        coder.io = FakeIO()
        coder.verbose = True
        coder.aider_edited_files = {"src/queue.hpp"}
        coder.last_verification = mock.Mock(meaningful_pass=False)
        coder.last_aider_commit_hash = "abc123"
        coder._z_gate_hold_dirty = False
        skips = []
        coder._skill_capture_skip = lambda r: skips.append(r)
        # Should reach confirm_ask (declined) — not early-return on verify
        coder._maybe_suggest_skill(
            "Fix intermittent segfault in the lock-free producer queue under load"
        )
        self.assertFalse(
            any("verify incomplete" in s for s in skips),
            skips,
        )

    def test_suggest_skips_when_no_commit_and_verify_failed(self):
        from aider.coders.base_coder import Coder

        class FakeIO:
            yes = None

            def confirm_ask(self, *a, **k):
                raise AssertionError("should not prompt")

            def tool_output(self, *a, **k):
                pass

        coder = Coder.__new__(Coder)
        coder.io = FakeIO()
        coder.verbose = True
        coder.aider_edited_files = {"a.cpp"}
        coder.last_verification = mock.Mock(meaningful_pass=False)
        coder.last_aider_commit_hash = None
        coder._z_gate_hold_dirty = False
        skips = []
        coder._skill_capture_skip = lambda r: skips.append(r)
        coder._maybe_suggest_skill("Fix the segfault in the background thread")
        self.assertTrue(any("verify incomplete" in s for s in skips), skips)

    def test_yes_always_skips_ordinary_playbook_capture(self):
        from aider.coders.base_coder import Coder

        class FakeIO:
            yes = True

            def confirm_ask(self, *a, **k):
                raise AssertionError("should not prompt under yes-always")

            def tool_output(self, *a, **k):
                pass

        coder = Coder.__new__(Coder)
        coder.io = FakeIO()
        coder.verbose = True
        coder.aider_edited_files = {"src/rate_limiter.py"}
        coder.last_verification = mock.Mock(meaningful_pass=True)
        coder.last_aider_commit_hash = "abc123"
        coder._z_gate_hold_dirty = False
        skips = []
        coder._skill_capture_skip = lambda r: skips.append(r)
        coder._maybe_suggest_skill(
            "Add a sliding-window rate limiter middleware for the API"
        )
        self.assertTrue(any("yes_always" in s or "--yes" in s for s in skips), skips)

    def test_yes_always_auto_captures_bug_pattern(self):
        """CI/--yes-always must still accumulate bug_pattern organizational memory."""
        from aider.coders.base_coder import Coder
        from aider.z.skills.schema import SKILL_KIND_BUG_PATTERN, Skill

        outputs = []

        class FakeIO:
            yes = True

            def confirm_ask(self, *a, **k):
                raise AssertionError(
                    "bug_pattern under yes-always must not prompt confirm_ask"
                )

            def tool_output(self, *a, **k):
                outputs.append(a[0] if a else "")

            def tool_warning(self, *a, **k):
                pass

            def tool_error(self, *a, **k):
                pass

        coder = Coder.__new__(Coder)
        coder.io = FakeIO()
        coder.verbose = True
        coder.aider_edited_files = {"src/fmtlog.cpp"}
        coder.last_verification = mock.Mock(meaningful_pass=True)
        coder.last_aider_commit_hash = "def456"
        coder._z_gate_hold_dirty = False
        coder.root = None
        coder.repo = None
        coder.main_model = None
        coder.uncertainty_engine = None
        skips = []
        coder._skill_capture_skip = lambda r: skips.append(r)

        fake_skill = Skill(
            title="Missing sync on size",
            description="race",
            content="atomics",
            kind=SKILL_KIND_BUG_PATTERN,
            shared=True,
        )
        captured = {}

        def fake_save(io, topic, **kwargs):
            captured["topic"] = topic
            captured.update(kwargs)
            return fake_skill, True

        with mock.patch(
            "aider.z.skills.cli.save_skill_from_task", side_effect=fake_save
        ), mock.patch(
            "aider.z.skills.cli.offer_view_new_skill"
        ), mock.patch(
            "aider.z.skills.grounding.build_grounding_pack",
            return_value=mock.Mock(files=[], diff=""),
        ):
            coder._maybe_suggest_skill(
                "Fix intermittent segfault from missing sync on shared size field"
            )

        self.assertFalse(
            any("yes_always" in s or "--yes" in s for s in skips),
            skips,
        )
        self.assertTrue(captured.get("prefer_bug_pattern"), captured)
        self.assertTrue(
            any("auto-capturing bug_pattern" in str(o).lower() for o in outputs),
            outputs,
        )


class TaxonomyTest(unittest.TestCase):
    def test_core_categories_present(self):
        ids = set(taxonomy_category_ids())
        self.assertIn("missing_synchronization_for_shared_state", ids)
        self.assertIn("use_after_free", ids)
        self.assertGreaterEqual(len(BUG_CONCEPTS), 4)

    def test_language_note_cpp_sync(self):
        note = language_note("missing_synchronization_for_shared_state", "cpp")
        self.assertIsNotNone(note)
        self.assertIn("atomic", note.lower())

    def test_category_grounded_on_atomic_diff(self):
        ok, reason = category_grounded_in_diff(
            "missing_synchronization_for_shared_state", _ATOMIC_DIFF
        )
        self.assertTrue(ok, reason)

    def test_category_ungrounded_without_evidence(self):
        ok, reason = category_grounded_in_diff(
            "missing_synchronization_for_shared_state",
            "diff --git a/x.py b/x.py\n+def add(a,b): return a+b\n",
        )
        self.assertFalse(ok, reason)

    def test_boost_for_matching_keywords(self):
        boosted = boost_for_category(
            0.5,
            "missing_synchronization_for_shared_state",
            "intermittent segfault under ThreadSanitizer race",
        )
        self.assertGreater(boosted, 0.5)


class BugPatternGroundingTest(unittest.TestCase):
    def test_grounded_bug_pattern_ok(self):
        skill = Skill(
            title="SPSC visibility",
            description="race",
            content="Use atomics",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_category="missing_synchronization_for_shared_state",
            symptom_description="Intermittent crash in consumer thread",
            fix_technique="convert bare flag to atomic with release/acquire",
            verification_method="ThreadSanitizer before/after",
            language="cpp",
        )
        pack = GroundingPack(diff=_ATOMIC_DIFF, symbols=["size"])
        result = check_bug_pattern_grounding(skill, pack)
        self.assertTrue(result.ok, result.reason)
        # Taxonomy labels stay on root_cause_category — never in grounded_symbols
        self.assertNotIn(
            "missing_synchronization_for_shared_state",
            result.grounded_symbols,
        )

    def test_apply_pack_metadata_strips_taxonomy_from_grounded_symbols(self):
        from aider.z.skills.generate import _apply_pack_metadata
        from aider.z.skills.grounding import FileEvidence, GroundingResult

        skill = Skill(
            title="SPSC visibility",
            description="race",
            content="Use atomics",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_category="missing_synchronization_for_shared_state",
        )
        pack = GroundingPack(
            diff=_ATOMIC_DIFF,
            symbols=["size", "missing_synchronization_for_shared_state"],
            files=[FileEvidence(path="fmtlog-inl.h", content=_ATOMIC_DIFF)],
        )
        result = GroundingResult(
            ok=True,
            grounded_symbols=[
                "size",
                "missing_synchronization_for_shared_state",
            ],
            missing_symbols=[],
            invented_ratio=0.0,
            reason="ok",
        )
        _apply_pack_metadata(skill, pack, result)
        self.assertEqual(skill.grounded_symbols, ["size"])
        self.assertNotIn(
            "missing_synchronization_for_shared_state",
            skill.grounded_symbols,
        )

    def test_ungrounded_category_needs_review(self):
        skill = Skill(
            title="claim",
            description="x",
            content="x",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_category="missing_synchronization_for_shared_state",
        )
        pack = GroundingPack(diff="+print('hi')\n", symbols=[])
        result = check_bug_pattern_grounding(skill, pack)
        self.assertFalse(result.ok)


class IntentAndRouteTest(unittest.TestCase):
    def test_bugfix_intent_detects_segfault(self):
        self.assertTrue(
            task_is_bugfix_intent("Fix intermittent segfault in polling thread")
        )
        self.assertFalse(task_is_bugfix_intent("Add a rate limiter middleware"))

    def test_bug_pattern_routes_only_on_bugfix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
            (root / "q.cpp").write_text("int x;\n", encoding="utf-8")
            sig = collect_repo_signals(root)
            skill = Skill(
                title="SPSC race",
                description="race",
                content="atomics",
                kind=SKILL_KIND_BUG_PATTERN,
                quality_state="verified",
                needs_review=False,
                root_cause_category="missing_synchronization_for_shared_state",
                symptom_description="consumer crashes under load",
                language="cpp",
                languages=["cpp"],
                shared=True,
            )
            d_bug = route_skill(
                skill,
                "segfault in background polling thread under race",
                sig,
                score=0.7,
            )
            self.assertTrue(d_bug.apply, d_bug.reason)
            d_feat = route_skill(
                skill, "Add a new HTTP endpoint for health", sig, score=0.7
            )
            self.assertFalse(d_feat.apply)

    def test_bug_pattern_transfers_across_languages(self):
        """C++-captured sync pattern must not hard-fail language/stack in Rust.

        Live failure: languages=[cpp] vs rust task → 'language/stack mismatch'
        even with matching root_cause_category.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "job_queue"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            (root / "src").mkdir()
            (root / "src" / "lib.rs").write_text(
                "static mut READY: bool = false;\n", encoding="utf-8"
            )
            sig = collect_repo_signals(root)
            skill = Skill(
                title="Volatile publication flags fail to synchronize",
                description="producer/consumer race on ready flag",
                content="Use acquire/release atomics",
                kind=SKILL_KIND_BUG_PATTERN,
                quality_state="verified",
                needs_review=False,
                shared=True,
                repo_key="",
                languages=["cpp"],
                language="cpp",
                root_cause_category="missing_synchronization_for_shared_state",
                symptom_description=(
                    "consumer observes stale ready flag; intermittent race"
                ),
            )
            self.assertTrue(
                language_compatible(
                    skill,
                    sig,
                    task="Fix data race on ready flag between producer and consumer in Rust",
                )
            )
            d = route_skill(
                skill,
                "Fix data race on ready flag between producer and consumer in Rust",
                sig,
                score=0.85,
            )
            self.assertTrue(d.apply, d.reason)
            self.assertNotIn("language", d.reason.lower())

    def test_playbook_still_hard_filters_language(self):
        """Ordinary playbooks remain language-bound."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
            )
            (root / "src").mkdir()
            (root / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
            sig = collect_repo_signals(root)
            skill = Skill(
                title="Stripe webhook validation",
                description="How this Python API validates Stripe webhooks",
                content="Use stripe.Webhook.construct_event",
                kind="playbook",
                quality_state="verified",
                needs_review=False,
                languages=["python"],
            )
            self.assertFalse(
                language_compatible(
                    skill, sig, task="Add Stripe webhook validation in Rust"
                )
            )
            d = route_skill(
                skill, "Add Stripe webhook validation in Rust", sig, score=0.9
            )
            self.assertFalse(d.apply)
            self.assertIn("language", d.reason.lower())


class HypothesisFormatTest(unittest.TestCase):
    def test_hypothesis_includes_language_note(self):
        skill = Skill(
            title="SPSC",
            description="race",
            content="fix",
            kind=SKILL_KIND_BUG_PATTERN,
            symptom_description="Intermittent crash when log call is last in member fn",
            root_cause_category="missing_synchronization_for_shared_state",
            fix_technique="atomic release/acquire",
            verification_method="TSan before/after",
            language="cpp",
        )
        text = format_bug_pattern_hypothesis(skill)
        self.assertIn("previously-solved bug", text.lower())
        self.assertIn("missing_synchronization_for_shared_state", text)
        self.assertIn("atomic", text.lower())
        block = format_skills_for_context([skill])
        self.assertIn("hypothesis", block.lower())
        self.assertNotIn("Follow them where relevant", block)

    def test_embed_text_uses_symptom(self):
        skill = Skill(
            title="SPSC",
            description="desc",
            content="body",
            kind=SKILL_KIND_BUG_PATTERN,
            symptom_description="consumer sees stale size under load",
            root_cause_category="missing_synchronization_for_shared_state",
        )
        emb = skill.embed_text()
        self.assertIn("consumer sees stale size", emb)
        self.assertNotIn("body", emb)


class PersistRoundTripTest(unittest.TestCase):
    def test_store_round_trips_bug_pattern_fields(self):
        with tempfile.TemporaryDirectory() as td:
            store = LocalSkillStore(root=Path(td))
            skill = Skill(
                title="SPSC visibility race",
                description="race",
                content="## Fix\nUse atomics\n",
                kind=SKILL_KIND_BUG_PATTERN,
                symptom_description="stale size across threads",
                root_cause_category="missing_synchronization_for_shared_state",
                root_cause_explanation="volatile publish had no release semantics",
                fix_technique="std::atomic + release/acquire",
                verification_method="ThreadSanitizer before/after",
                language="cpp",
                languages=["cpp"],
                quality_state="verified",
            )
            store.save(skill)
            loaded = store.get(skill.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.kind, SKILL_KIND_BUG_PATTERN)
            self.assertEqual(
                loaded.root_cause_category,
                "missing_synchronization_for_shared_state",
            )
            self.assertIn("stale size", loaded.symptom_description)

    def test_markdown_round_trip(self):
        md = """---
id: abc
title: Leak pattern
description: leak
kind: bug_pattern
language: cpp
root_cause_category: resource_leak
symptom_description: RSS grows without bound
fix_technique: RAII unique_ptr
verification_method: LeakSanitizer
---
## Notes
Use RAII.
"""
        skill = skill_from_markdown(md, filename="leak.md")
        self.assertEqual(skill.kind, SKILL_KIND_BUG_PATTERN)
        self.assertEqual(skill.root_cause_category, "resource_leak")


class GateHoldClearTest(unittest.TestCase):
    def test_medium_ack_clears_hold(self):
        """Regression: medium-ack used to leave _z_gate_hold_dirty stuck."""
        import inspect

        from aider.z.uncertainty import gate as gate_mod

        src = inspect.getsource(gate_mod.prepare_commit)
        # Every allow_commit=True path after human ack should clear the hold
        self.assertIn("coder._z_gate_hold_dirty = False", src)
        # Count clears — clean success + force (×2) + medium ack ≥ 3
        self.assertGreaterEqual(src.count("coder._z_gate_hold_dirty = False"), 3)


if __name__ == "__main__":
    unittest.main()
