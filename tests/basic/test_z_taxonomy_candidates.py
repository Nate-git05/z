"""Taxonomy blind-spot learning — grounding miss → accept → review."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_taxonomy_")
os.environ["Z_HOME"] = _HOME

from aider.z.skills.bug_concepts import category_grounded_in_diff  # noqa: E402
from aider.z.skills.grounding import (  # noqa: E402
    GroundingPack,
    check_bug_pattern_grounding,
    extract_call_site_names,
)
from aider.z.skills.schema import SKILL_KIND_BUG_PATTERN, Skill  # noqa: E402
from aider.z.skills.store import LocalSkillStore, skill_from_markdown, skill_to_markdown  # noqa: E402
from aider.z.skills.taxonomy_candidates import (  # noqa: E402
    candidate_terms_from_blob,
    format_taxonomy_review,
    latest_miss_for_skill,
    list_candidates,
    record_confirmation_candidate,
    record_grounding_miss,
)


# Blind-spot shape still outside use_after_free evidence_regex after the
# handle/resolve/generation expansion (lookup_live is not covered).
_LOOKUP_LIVE_DIFF = (
    "diff --git a/src/table.cpp b/src/table.cpp\n"
    "--- a/src/table.cpp\n"
    "+++ b/src/table.cpp\n"
    "@@ -10,6 +10,8 @@\n"
    "+    // re-fetch live entry instead of caching a dangling pointer\n"
    "+    auto *obj = table.lookup_live(id);\n"
    "+    return obj->value;\n"
)


class CallSiteExtractTest(unittest.TestCase):
    def test_extracts_method_calls(self):
        names = extract_call_site_names(
            "entries_.pop_back();\ntable.lookup_live(id);\nx.clear();\n"
        )
        self.assertIn("pop_back", names)
        self.assertIn("lookup_live", names)
        self.assertIn("clear", names)


class GroundingMissHookTest(unittest.TestCase):
    def test_evidence_mismatch_records_miss_and_reason(self):
        skill = Skill(
            title="Lookup-live UAF",
            description="dangling",
            content="Use table.lookup_live()",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_category="use_after_free",
            symptom_description="ASan use-after-free on cached pointer",
        )
        pack = GroundingPack(
            user_request="fix UAF",
            diff=_LOOKUP_LIVE_DIFF,
            symbols=["lookup_live"],
        )
        # Confirm category is known but regex does not ground this idiom
        ok, reason = category_grounded_in_diff("use_after_free", _LOOKUP_LIVE_DIFF)
        self.assertFalse(ok, reason)

        result = check_bug_pattern_grounding(skill, pack)
        self.assertFalse(result.ok)
        self.assertTrue(
            (skill.grounding_miss_reason or "").startswith("diff lacks evidence"),
            skill.grounding_miss_reason,
        )
        miss = latest_miss_for_skill(skill.id)
        self.assertIsNotNone(miss)
        self.assertEqual(miss["category_id"], "use_after_free")
        self.assertIn("lookup_live", miss["added_diff_blob"])

    def test_unknown_category_does_not_set_miss_reason(self):
        skill = Skill(
            title="x",
            description="x",
            content="x",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_category="not_a_real_category",
        )
        pack = GroundingPack(diff=_LOOKUP_LIVE_DIFF)
        result = check_bug_pattern_grounding(skill, pack)
        self.assertFalse(result.ok)
        self.assertIsNone(skill.grounding_miss_reason)

    def test_successful_ground_clears_prior_miss_reason(self):
        skill = Skill(
            title="x",
            description="x",
            content="free on all paths",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_category="resource_leak",
            grounding_miss_reason="diff lacks evidence for resource_leak …",
        )
        pack = GroundingPack(
            diff=(
                "diff --git a/a.cpp b/a.cpp\n"
                "--- a/a.cpp\n+++ b/a.cpp\n"
                "@@ -1 +1,2 @@\n"
                "+    free(p);\n"
            )
        )
        result = check_bug_pattern_grounding(skill, pack)
        self.assertTrue(result.ok, result.reason)
        self.assertIsNone(skill.grounding_miss_reason)


class CandidateConfirmTest(unittest.TestCase):
    def setUp(self):
        # Isolate candidate/miss files per test under Z_HOME
        self._dir = Path(tempfile.mkdtemp(prefix="z_tax_case_"))
        os.environ["Z_HOME"] = str(self._dir)
        # Re-import path helpers pick up ensure_z_home() live — files are under Z_HOME

    def test_drops_terms_already_in_evidence_regex(self):
        # resource_leak already knows pop_back after container-idiom fix
        terms = candidate_terms_from_blob(
            "resource_leak",
            "entries_.pop_back();\nentries_.erase(it);\nhandle.resolve();\n",
        )
        self.assertNotIn("pop_back", terms)
        self.assertNotIn("erase", terms)
        self.assertIn("resolve", terms)

    def test_confirmation_counts_distinct_skills_only(self):
        blob = "auto *o = table.lookup_live(id);\n"
        record_confirmation_candidate(
            "use_after_free", blob, "skill-a", skill_title="A"
        )
        # Same skill again — must not double-count
        record_confirmation_candidate(
            "use_after_free", blob, "skill-a", skill_title="A"
        )
        record_confirmation_candidate(
            "use_after_free", blob, "skill-b", skill_title="B"
        )
        by_cat = list_candidates(min_count=2)
        self.assertIn("use_after_free", by_cat)
        live = next(c for c in by_cat["use_after_free"] if c.term == "lookup_live")
        self.assertEqual(live.count, 2)
        self.assertEqual(set(live.skill_ids), {"skill-a", "skill-b"})

    def test_list_candidates_respects_min_count(self):
        blob = "cache.refresh();\n"
        record_confirmation_candidate(
            "use_after_free", blob, "only-one", skill_title="Solo"
        )
        self.assertEqual(list_candidates(min_count=2).get("use_after_free", []), [])
        self.assertTrue(list_candidates(min_count=1).get("use_after_free"))

    def test_format_review_mentions_term(self):
        blob = "table.rebuild();\n"
        record_confirmation_candidate(
            "use_after_free", blob, "s1", skill_title="One"
        )
        record_confirmation_candidate(
            "use_after_free", blob, "s2", skill_title="Two"
        )
        text = format_taxonomy_review(min_count=2)
        self.assertIn("rebuild", text)
        self.assertIn("use_after_free", text)


class AcceptConfirmHookTest(unittest.TestCase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="z_accept_tax_home_"))
        os.environ["Z_HOME"] = str(self._dir)

    def test_accept_records_candidates_from_miss_blob(self):
        root = Path(tempfile.mkdtemp(prefix="z_accept_tax_"))
        store = LocalSkillStore(root=root)
        skill = Skill(
            title="Lookup-live on demand",
            description="UAF",
            content="body",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_category="use_after_free",
            quality_state="draft",
            needs_review=True,
            grounding_miss_reason="diff lacks evidence for use_after_free …",
        )
        store.save(skill)
        record_grounding_miss(
            "use_after_free",
            "auto *o = table.lookup_live(id);\n",
            skill.id,
            skill_title=skill.title,
        )

        class FakeIO:
            def __init__(self):
                self.lines = []

            def tool_output(self, *a, **k):
                self.lines.append(" ".join(str(x) for x in a))

            def tool_error(self, *a, **k):
                pass

            def prompt_ask(self, *a, **k):
                return ""

        from aider.z.skills.cli import accept_skill

        io = FakeIO()
        with mock.patch("aider.z.skills.cli.LocalSkillStore", return_value=store), mock.patch(
            "aider.z.skills.cli.upsert_skill_vector"
        ):
            rc = accept_skill(io, skill.id)
        self.assertEqual(rc, 0)
        # One accept alone is not enough to surface at min_count=2
        self.assertEqual(list_candidates(min_count=2).get("use_after_free", []), [])
        # But the term was counted once
        one = list_candidates(min_count=1)["use_after_free"]
        self.assertTrue(any(c.term == "lookup_live" and c.count == 1 for c in one))
        self.assertTrue(
            any("Taxonomy candidates" in line for line in io.lines),
            io.lines,
        )


class PersistMissReasonTest(unittest.TestCase):
    def test_roundtrip_frontmatter(self):
        skill = Skill(
            title="t",
            description="d",
            content="body",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_category="use_after_free",
            grounding_miss_reason="diff lacks evidence for use_after_free (…)",
        )
        again = skill_from_markdown(skill_to_markdown(skill))
        self.assertEqual(
            again.grounding_miss_reason,
            "diff lacks evidence for use_after_free (…)",
        )


class TaxonomyCliTest(unittest.TestCase):
    def test_cmd_taxonomy_review(self):
        from aider.z.cli import cmd_taxonomy

        class Args:
            taxonomy_command = "review"
            min_count = 2

        class FakeIO:
            def __init__(self):
                self.out = []

            def tool_output(self, *a, **k):
                self.out.append(" ".join(str(x) for x in a))

            def tool_error(self, *a, **k):
                pass

        io = FakeIO()
        rc = cmd_taxonomy(io, Args())
        self.assertEqual(rc, 0)
        self.assertTrue(io.out)


if __name__ == "__main__":
    unittest.main()
