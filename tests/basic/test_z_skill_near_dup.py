"""Skill lexical fallback + near-dup capture merge (skill-retrieve slice)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

from aider.z.skills.near_dup import (
    bug_field_score,
    find_near_dup,
    jaccard,
    lexical_match_skills,
    merge_into_existing,
    near_dup_enabled,
    title_similarity,
    tokenize_folded,
)
from aider.z.skills.schema import SKILL_KIND_BUG_PATTERN, Skill, SkillIndexEntry
from aider.z.skills.session import (
    clear_session_skills,
    retrieve_skill_candidates,
)
from aider.z.skills.store import LocalSkillStore


class StemFoldingTest(unittest.TestCase):
    def test_lru_lfu_share_family(self):
        a = tokenize_folded("release backing storage during lru eviction")
        b = tokenize_folded("implement lfu cache eviction that frees backing storage")
        self.assertIn("fam:cache_policy", a)
        self.assertIn("fam:cache_policy", b)
        self.assertIn("fam:eviction", a)
        self.assertIn("fam:eviction", b)
        self.assertGreater(jaccard(a, b), 0.15)

    def test_title_similarity_folded(self):
        sim = title_similarity(
            "release-backing-storage-during-lru-eviction",
            "lfu cache eviction frees backing storage",
        )
        self.assertGreater(sim, 0.1)


class LexicalRetrieveTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("Z_SKILL_LEXICAL_FALLBACK", None)
        os.environ.pop("Z_SKILL_LEXICAL_THRESHOLD", None)
        clear_session_skills()

    def tearDown(self):
        clear_session_skills()
        os.environ.pop("Z_SKILL_LEXICAL_FALLBACK", None)

    def test_lexical_retrieves_lru_skill_for_lfu_task(self):
        entry = SkillIndexEntry(
            id="lru-1",
            title="release-backing-storage-during-lru-eviction",
            description="Free backing pages when LRU cache evicts an entry",
            kind=SKILL_KIND_BUG_PATTERN,
            tags=["cache", "eviction", "lru"],
            triggers=["lru", "evict", "backing storage"],
            symptom_description=(
                "Cache eviction drops the key but leaves backing storage "
                "allocated; memory grows under LRU pressure"
            ),
            root_cause_category="resource_not_released_on_eviction",
            fix_technique="Release backing storage in the eviction path",
        )
        task = (
            "implement LFU cache eviction that frees backing storage "
            "when an entry is removed"
        )
        hits = lexical_match_skills(task, [entry], kind=SKILL_KIND_BUG_PATTERN, limit=3)
        self.assertTrue(hits, "expected lexical hit for LFU task vs LRU skill")
        self.assertEqual(hits[0][0].id, "lru-1")
        self.assertGreaterEqual(hits[0][1], 0.28)

    def test_retrieve_with_chroma_stubbed_empty_uses_lexical(self):
        from aider.z.skills import session as sess

        entry = SkillIndexEntry(
            id="lru-2",
            title="release-backing-storage-during-lru-eviction",
            description="LRU eviction must free backing storage",
            kind=SKILL_KIND_BUG_PATTERN,
            tags=["lru", "cache", "eviction"],
            symptom_description="backing storage not released on cache eviction",
            root_cause_category="resource_not_released_on_eviction",
            fix_technique="free backing buffer in evict()",
            path=None,
        )
        # Seed session index
        sess._SESSION_INDEX[:] = [entry]

        fake = MagicMock()
        fake.available = True
        fake.count.return_value = 0
        fake.query.return_value = []

        with mock.patch(
            "aider.z.skills.session.get_skill_vector_index", return_value=fake
        ):
            with mock.patch(
                "aider.z.skills.session.resolve_full_skill",
                side_effect=lambda e: Skill(
                    id=e.id,
                    title=e.title,
                    description=e.description or "",
                    content="",
                    kind=e.kind,
                    symptom_description=e.symptom_description,
                    root_cause_category=e.root_cause_category,
                    fix_technique=e.fix_technique,
                    tags=list(e.tags or []),
                ),
            ):
                got = retrieve_skill_candidates(
                    "LFU cache eviction must free backing storage",
                    kind=SKILL_KIND_BUG_PATTERN,
                    limit=3,
                )
        self.assertTrue(got)
        self.assertEqual(got[0][0].id, "lru-2")


class NearDupCaptureTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("Z_SKILL_NEAR_DUP", None)
        self.root = Path(tempfile.mkdtemp(prefix="z_skill_dup_"))
        self.store = LocalSkillStore(root=self.root)

    def tearDown(self):
        os.environ.pop("Z_SKILL_NEAR_DUP", None)

    def test_find_near_dup_same_category_title(self):
        existing = Skill(
            id="aaa11111-bbbb-cccc-dddd-eeeeeeeeeeee",
            title="release backing storage during lru eviction",
            description="old",
            content="body",
            kind=SKILL_KIND_BUG_PATTERN,
            symptom_description="backing storage left after LRU eviction",
            root_cause_category="resource_not_released_on_eviction",
            shared=True,
        )
        new = Skill(
            id="ffff9999-bbbb-cccc-dddd-eeeeeeeeeeee",
            title="release backing storage on lfu eviction",
            description="new",
            content="body",
            kind=SKILL_KIND_BUG_PATTERN,
            symptom_description="backing storage left after LFU cache eviction",
            root_cause_category="resource_not_released_on_eviction",
            shared=True,
        )
        hit = find_near_dup(new, [existing])
        self.assertIsNotNone(hit)
        self.assertEqual(hit.skill.id, existing.id)

    def test_merge_keeps_id_and_appends(self):
        existing = Skill(
            id="aaa11111-bbbb-cccc-dddd-eeeeeeeeeeee",
            title="lru eviction storage",
            description="lru",
            content="Original body.\n",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_explanation="First diagnosis.",
            tags=["lru"],
            source_files=["a.c"],
        )
        new = Skill(
            title="lfu eviction storage",
            description="lfu",
            content="New capture body.\n",
            kind=SKILL_KIND_BUG_PATTERN,
            root_cause_explanation="Second diagnosis from LFU case.",
            tags=["lfu"],
            source_files=["b.c"],
            fix_technique="Call free_backing() in evict",
        )
        merged = merge_into_existing(existing, new)
        self.assertEqual(merged.id, "aaa11111-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertIn("Second diagnosis", merged.root_cause_explanation)
        self.assertIn("lfu", merged.tags)
        self.assertIn("b.c", merged.source_files)
        self.assertIn("Additional evidence", merged.content)

    def test_capture_near_dup_updates_not_creates(self):
        from aider.z.skills.cli import save_skill_from_task

        existing = Skill(
            id="aaa11111-bbbb-cccc-dddd-eeeeeeeeeeee",
            title="release backing storage during lru eviction",
            description="Free backing on LRU eviction",
            kind=SKILL_KIND_BUG_PATTERN,
            symptom_description="backing storage not released on cache eviction",
            root_cause_category="resource_not_released_on_eviction",
            content="Original.\n",
            shared=True,
            quality_state="draft",
            needs_review=True,
        )
        self.store.save(existing)
        before = len(self.store.list_skills())

        io = MagicMock()
        generated = Skill(
            title="release backing storage during lfu eviction",
            description="Free backing on LFU eviction",
            kind=SKILL_KIND_BUG_PATTERN,
            symptom_description="backing storage not released on LFU cache eviction",
            root_cause_category="resource_not_released_on_eviction",
            content="Clone attempt.\n",
            shared=True,
        )

        with mock.patch(
            "aider.z.skills.cli.generate_skill",
            return_value=(generated, None, None),
        ):
            with mock.patch(
                "aider.z.skills.cli.LocalSkillStore",
                return_value=self.store,
            ):
                with mock.patch("aider.z.skills.cli.upsert_skill_vector", return_value=True):
                    with mock.patch("aider.z.skills.cli._stamp_repo_key"):
                        skill, created = save_skill_from_task(
                            io,
                            "LFU eviction frees storage",
                            prefer_bug_pattern=True,
                        )
        self.assertFalse(created)
        self.assertEqual(skill.id, existing.id)
        self.assertEqual(len(self.store.list_skills()), before)
        io.tool_output.assert_any_call(
            mock.ANY
        )
        # At least one call mentions Updated existing skill
        printed = " ".join(
            str(c.args[0]) for c in io.tool_output.call_args_list if c.args
        )
        self.assertIn("Updated existing skill", printed)

    def test_near_dup_disabled_creates_new(self):
        os.environ["Z_SKILL_NEAR_DUP"] = "0"
        self.assertFalse(near_dup_enabled())
        from aider.z.skills.cli import save_skill_from_task

        existing = Skill(
            id="aaa11111-bbbb-cccc-dddd-eeeeeeeeeeee",
            title="release backing storage during lru eviction",
            description="Free backing on LRU eviction",
            kind=SKILL_KIND_BUG_PATTERN,
            symptom_description="backing storage not released",
            root_cause_category="resource_not_released_on_eviction",
            content="Original.\n",
            shared=True,
        )
        self.store.save(existing)
        before = len(self.store.list_skills())

        io = MagicMock()
        generated = Skill(
            title="release backing storage during lfu eviction",
            description="Free backing on LFU eviction",
            kind=SKILL_KIND_BUG_PATTERN,
            symptom_description="backing storage not released on LFU",
            root_cause_category="resource_not_released_on_eviction",
            content="New.\n",
            shared=True,
        )

        with mock.patch(
            "aider.z.skills.cli.generate_skill",
            return_value=(generated, None, None),
        ):
            with mock.patch(
                "aider.z.skills.cli.LocalSkillStore",
                return_value=self.store,
            ):
                with mock.patch(
                    "aider.z.skills.cli._persist_skill",
                    side_effect=lambda io, skill, sync=True: self.store.save(skill)
                    or skill,
                ):
                    with mock.patch("aider.z.skills.cli._stamp_repo_key"):
                        _skill, created = save_skill_from_task(
                            io,
                            "LFU eviction",
                            prefer_bug_pattern=True,
                        )
        # When near-dup off, we go through _persist_skill which we stubbed to save
        self.assertTrue(created)
        self.assertEqual(len(self.store.list_skills()), before + 1)


class BugFieldScoreTest(unittest.TestCase):
    def test_score_uses_symptom_and_category(self):
        entry = SkillIndexEntry(
            id="1",
            title="x",
            description="",
            symptom_description="cache eviction leaves backing storage",
            root_cause_category="resource_not_released_on_eviction",
            tags=["eviction"],
        )
        score, reason = bug_field_score(
            "LFU cache eviction must free backing storage", entry
        )
        self.assertGreater(score, 0.2)
        self.assertTrue(reason)


if __name__ == "__main__":
    unittest.main()
