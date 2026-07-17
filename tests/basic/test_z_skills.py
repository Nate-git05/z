"""Tests for Z skills — store, infer, ChromaDB index, session pull, API."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_DB_PATH = tempfile.mktemp(suffix="_z_skills_test.db")
_SKILLS_HOME = tempfile.mkdtemp(prefix="z_skills_home_")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
os.environ["Z_SECRET_KEY"] = "test-secret-skills"
os.environ["Z_SERVER_DEV"] = "1"
os.environ["Z_PUBLIC_BASE_URL"] = "http://testserver"
os.environ["Z_HOME"] = _SKILLS_HOME

from z_server.config import get_settings  # noqa: E402

get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from aider.z.skills.cli import (  # noqa: E402
    cmd_skill_add,
    cmd_skill_create,
    offer_view_new_skill,
)
from aider.z.skills.index import match_skills, relevance_score  # noqa: E402
from aider.z.skills.infer import apply_inferred_metadata, infer_metadata  # noqa: E402
from aider.z.skills.schema import Skill, SkillIndexEntry  # noqa: E402
from aider.z.skills.session import (  # noqa: E402
    format_skill_metadata,
    format_skills_for_context,
    load_skills_for_session,
    resolve_full_skill,
    select_relevant_skills,
)
from aider.z.skills.store import LocalSkillStore, skill_from_markdown, skill_to_markdown  # noqa: E402
from aider.z.skills.vector import SkillVectorIndex  # noqa: E402
from z_server.app import create_app  # noqa: E402
from z_server.db import init_db, reset_engine  # noqa: E402


class LocalStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="z_skills_local_"))
        self.store = LocalSkillStore(root=self.root)

    def test_save_and_list_sets_path(self):
        skill = Skill(
            title="Stripe webhook validation",
            description="How this repo validates Stripe webhooks",
            content="## Steps\n1. Verify signature\n2. Handle idempotency\n",
            tags=["stripe", "webhooks"],
            triggers=["stripe", "webhook", "signature"],
            project_types=["api", "backend"],
            source="paste",
        )
        path = self.store.save(skill)
        self.assertTrue(path.is_file())
        self.assertEqual(skill.path, str(path.resolve()))
        listed = self.store.list_skills()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].title, "Stripe webhook validation")
        self.assertEqual(listed[0].tags, ["stripe", "webhooks"])
        self.assertTrue(listed[0].path.endswith(".md"))
        loaded = self.store.get(skill.id)
        self.assertIsNotNone(loaded)
        self.assertIn("Verify signature", loaded.content)

    def test_roundtrip_markdown_preserves_metadata(self):
        skill = Skill(
            title="Migration scripts",
            description="Team convention for Alembic migrations",
            content="Always expand then contract.",
            created_by="Ada",
            tags=["alembic", "migrations"],
            triggers=["migration", "alembic"],
            project_types=["backend"],
            path="/tmp/example.md",
            source="generate",
        )
        text = skill_to_markdown(skill)
        again = skill_from_markdown(text, filename="x.md")
        self.assertEqual(again.title, skill.title)
        self.assertEqual(again.description, skill.description)
        self.assertEqual(again.tags, ["alembic", "migrations"])
        self.assertEqual(again.project_types, ["backend"])
        self.assertEqual(again.source, "generate")
        self.assertIn("expand then contract", again.content)

    def test_index_is_lightweight(self):
        self.store.save(
            Skill(title="A", description="desc A", content="long body " * 50)
        )
        idx = self.store.index()
        self.assertEqual(len(idx), 1)
        self.assertNotIn("content", idx[0])
        self.assertEqual(idx[0]["title"], "A")
        self.assertIn("path", idx[0])


class InferMetadataTest(unittest.TestCase):
    def test_infer_from_paste_body(self):
        body = "# Stripe webhooks\n\nVerify Stripe webhook signatures before handling events.\n"
        meta = infer_metadata(body, source="paste")
        self.assertIn("Stripe", meta["title"])
        self.assertTrue(meta["description"])
        self.assertTrue(meta["tags"])
        self.assertTrue(meta["project_types"])
        self.assertEqual(meta["source"], "paste")

    def test_apply_keeps_existing_title(self):
        skill = Skill(
            title="My title",
            description="",
            content="Handle OAuth refresh tokens carefully in the API layer.",
            source="paste",
        )
        apply_inferred_metadata(skill)
        self.assertEqual(skill.title, "My title")
        self.assertTrue(skill.description)
        self.assertTrue(skill.tags)


class RelevanceTest(unittest.TestCase):
    def test_keyword_match(self):
        entry = SkillIndexEntry(
            id="1",
            title="Stripe webhook validation",
            description="Verify Stripe webhook signatures and idempotency",
            tags=["stripe", "webhooks"],
            triggers=["stripe", "webhook", "signature"],
        )
        score = relevance_score("add stripe webhook handler with signature check", entry)
        self.assertGreaterEqual(score, 0.35)
        misses = relevance_score("refactor css layout for homepage", entry)
        self.assertLess(misses, 0.35)

    def test_match_skills_limit(self):
        index = [
            SkillIndexEntry(id="1", title="Stripe webhooks", description="payment hooks", triggers=["stripe"]),
            SkillIndexEntry(id="2", title="CSS layout", description="homepage styles"),
            SkillIndexEntry(id="3", title="Alembic migrations", description="schema changes"),
        ]
        hits = match_skills("fix stripe webhook signature", index, threshold=0.2)
        self.assertTrue(hits)
        self.assertEqual(hits[0][0].id, "1")


def _has_chromadb() -> bool:
    try:
        import chromadb  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_chromadb(), "chromadb not installed")
class ChromaVectorTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="z_skills_chroma_"))
        self.store = LocalSkillStore(root=self.root / "skills")
        self.vdir = self.root / "chroma"
        self.index = SkillVectorIndex(persist_dir=self.vdir)

    def test_upsert_query_and_path_resolve(self):
        skill = Skill(
            title="Stripe webhook validation",
            description="Verify Stripe webhook signatures and idempotency",
            content="Check the Stripe-Signature header.",
            tags=["stripe", "webhooks"],
            triggers=["stripe", "webhook"],
            project_types=["api"],
            source="paste",
        )
        path = self.store.save(skill)
        self.index.upsert(skill)
        hits = self.index.query("implement stripe webhook signature check", k=3)
        self.assertTrue(hits)
        entry, score = hits[0]
        self.assertEqual(entry.title, "Stripe webhook validation")
        self.assertEqual(entry.path, str(path.resolve()))
        self.assertGreater(score, 0.0)

        resolved = resolve_full_skill(entry)
        self.assertIsNotNone(resolved)
        self.assertIn("Stripe-Signature", resolved.content)

    def test_reindex(self):
        for title in ("Alpha skill", "Beta payments"):
            s = Skill(title=title, description=title, content=f"Body for {title}")
            self.store.save(s)
        n = self.index.reindex(self.store.list_skills())
        self.assertEqual(n, 2)
        self.assertEqual(self.index.count(), 2)


class SessionPullTest(unittest.TestCase):
    def test_select_and_format(self):
        root = Path(tempfile.mkdtemp(prefix="z_skills_sess_"))
        store = LocalSkillStore(root=root)
        skill = Skill(
            title="Stripe webhook validation",
            description="Validate Stripe webhooks in this repo",
            content="Check the Stripe-Signature header.",
            tags=["stripe"],
            triggers=["stripe", "webhook"],
        )
        store.save(skill)

        with mock.patch("aider.z.skills.session.LocalSkillStore", return_value=store):
            with mock.patch("aider.z.skills.session.fetch_skill_index", return_value=[]):
                with mock.patch("aider.z.skills.session._sync_local_to_chroma"):
                    load_skills_for_session()
                    # Force keyword path for deterministic unit test
                    with mock.patch(
                        "aider.z.skills.session.get_skill_vector_index"
                    ) as gv:
                        fake = mock.Mock()
                        fake.available = False
                        gv.return_value = fake
                        pulled = select_relevant_skills(
                            "implement stripe webhook validation endpoint"
                        )
        self.assertEqual(len(pulled), 1)
        block = format_skills_for_context(pulled)
        self.assertIn("Stripe webhook", block)
        self.assertIn("Stripe-Signature", block)

    def test_metadata_formatter(self):
        skill = Skill(
            title="T",
            description="D",
            content="body",
            path="/tmp/t.md",
            tags=["a"],
            triggers=["b"],
            project_types=["api"],
            source="capture",
        )
        text = format_skill_metadata(skill)
        self.assertIn("Skill: T", text)
        self.assertIn("path: /tmp/t.md", text)
        self.assertIn("source: capture", text)


class SkillAddCliTest(unittest.TestCase):
    def test_add_pastes_and_infers(self):
        root = Path(tempfile.mkdtemp(prefix="z_skills_add_"))
        store = LocalSkillStore(root=root)
        io = mock.MagicMock()
        body = "# OAuth refresh\n\nAlways rotate refresh tokens on the API.\n"

        with mock.patch("aider.z.skills.cli.LocalSkillStore", return_value=store):
            with mock.patch("aider.z.skills.cli.upsert_skill_vector", return_value=True):
                with mock.patch("aider.z.skills.cli.sync_skill", return_value=None):
                    code = cmd_skill_add(io, body, sync=False)
        self.assertEqual(code, 0)
        skills = store.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].source, "paste")
        self.assertTrue(skills[0].path)
        self.assertTrue(skills[0].tags)

    def test_offer_view_shows_metadata_only_when_yes(self):
        io = mock.MagicMock()
        io.confirm_ask.return_value = True
        skill = Skill(
            title="Captured",
            description="From task",
            content="SECRET BODY SHOULD NOT AUTO PRINT",
            path="/tmp/c.md",
            source="capture",
            tags=["x"],
        )
        offer_view_new_skill(io, skill)
        printed = " ".join(str(c.args[0]) for c in io.tool_output.call_args_list if c.args)
        self.assertIn("Captured", printed)
        self.assertIn("/tmp/c.md", printed)
        self.assertNotIn("SECRET BODY", printed)


class SkillsApiTest(unittest.TestCase):
    def setUp(self):
        reset_engine()
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_DB_PATH}"
        if os.path.exists(_DB_PATH):
            os.unlink(_DB_PATH)
        init_db()
        self.app = create_app()
        self.client = TestClient(self.app)
        start = self.client.post(
            "/v1/auth/email/start",
            json={"email": "skills@example.com", "name": "Skiller"},
        )
        self.assertEqual(start.status_code, 200)
        verify = self.client.post(
            "/v1/auth/email/verify",
            json={"email": "skills@example.com", "code": "000000", "name": "Skiller"},
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        self.headers = {"Authorization": f"Bearer {verify.json()['access_token']}"}

    def tearDown(self):
        reset_engine()
        get_settings.cache_clear()
        if os.path.exists(_DB_PATH):
            try:
                os.unlink(_DB_PATH)
            except OSError:
                pass

    def test_crud_and_index(self):
        create = self.client.post(
            "/v1/skills",
            headers=self.headers,
            json={
                "title": "Stripe webhook validation",
                "description": "How we validate Stripe webhooks",
                "content": "## Verify signature\nUse the endpoint secret.",
                "scope": "personal",
            },
        )
        self.assertEqual(create.status_code, 200, create.text)
        skill = create.json()["skill"]
        self.assertEqual(skill["scope"], "personal")
        self.assertIn("Verify signature", skill["content"])

        listed = self.client.get("/v1/skills", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        rows = listed.json()["skills"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("content", rows[0])

        got = self.client.get(f"/v1/skills/{skill['id']}", headers=self.headers)
        self.assertEqual(got.status_code, 200)
        self.assertIn("content", got.json()["skill"])

        patched = self.client.patch(
            f"/v1/skills/{skill['id']}",
            headers=self.headers,
            json={"description": "Updated desc"},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["skill"]["description"], "Updated desc")

        shared = self.client.post(
            f"/v1/skills/{skill['id']}/share",
            headers=self.headers,
        )
        self.assertEqual(shared.status_code, 200, shared.text)
        self.assertEqual(shared.json()["skill"]["scope"], "workspace")

        deleted = self.client.delete(
            f"/v1/skills/{skill['id']}",
            headers=self.headers,
        )
        self.assertEqual(deleted.status_code, 200)

    def test_skills_page(self):
        self.client.post(
            "/v1/skills",
            headers=self.headers,
            json={
                "title": "Migration convention",
                "description": "Alembic expand/contract",
                "content": "Never destructive-first.",
            },
        )
        token = self.headers["Authorization"].split(" ", 1)[1]
        self.client.cookies.set("z_session", token)
        page = self.client.get("/app/skills")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Migration convention", page.text)


class GenerateParseTest(unittest.TestCase):
    def test_generate_uses_model_json(self):
        from aider.z.skills.generate import generate_skill

        fake_response = (
            '{"title": "T", "description": "D", "content": "## Body\\nDo the thing.",'
            ' "tags": ["thing"], "triggers": ["thing"]}'
        )

        class FakeModel:
            def simple_send_with_retries(self, messages):
                return fake_response

        with mock.patch("aider.z.skills.generate.resolve_model", return_value=FakeModel()):
            skill, err, ground = generate_skill("how we do the thing")
        self.assertIsNone(err)
        self.assertIsNone(ground)
        self.assertEqual(skill.title, "T")
        self.assertIn("Do the thing", skill.content)
        self.assertIn("thing", skill.tags)

    def test_generate_bug_pattern_populates_structured_fields(self):
        from aider.z.skills.generate import generate_skill
        from aider.z.skills.schema import SKILL_KIND_BUG_PATTERN

        fake_response = (
            '{"title": "SPSC visibility", "description": "stale size race",'
            ' "content": "## Symptom\\nCrash under load\\n",'
            ' "kind": "bug_pattern",'
            ' "symptom_description": "Intermittent segfault when log is last statement",'
            ' "root_cause_category": "missing_synchronization_for_shared_state",'
            ' "root_cause_explanation": "volatile size had no release/acquire",'
            ' "fix_technique": "std::atomic with memory_order_release/acquire",'
            ' "verification_method": "ThreadSanitizer before/after",'
            ' "language": "cpp",'
            ' "tags": ["race", "tsan"]}'
        )

        class FakeModel:
            def simple_send_with_retries(self, messages):
                # Bug-pattern system prompt must be selected
                sys = messages[0]["content"]
                assert "BUG-PATTERN" in sys or "bug_pattern" in sys.lower()
                return fake_response

        with mock.patch("aider.z.skills.generate.resolve_model", return_value=FakeModel()):
            skill, err, ground = generate_skill(
                "segfault in polling thread — volatile size race fixed with atomics",
                prefer_bug_pattern=True,
            )
        self.assertIsNone(err)
        self.assertEqual(skill.kind, SKILL_KIND_BUG_PATTERN)
        self.assertIn("Intermittent segfault", skill.symptom_description)
        self.assertEqual(
            skill.root_cause_category, "missing_synchronization_for_shared_state"
        )
        self.assertIn("atomic", skill.fix_technique.lower())
        self.assertIn("ThreadSanitizer", skill.verification_method)
        self.assertEqual(skill.language, "cpp")


class SkillCreateBugPatternCliTest(unittest.TestCase):
    def test_create_routes_bugfix_topic_to_bug_pattern(self):
        root = Path(tempfile.mkdtemp(prefix="z_skills_create_bug_"))
        store = LocalSkillStore(root=root)
        io = mock.MagicMock()
        captured = {}

        def fake_generate(topic, **kwargs):
            captured.update(kwargs)
            captured["topic"] = topic
            skill = Skill(
                title="SPSC race",
                description="race",
                content="## Fix\natomics\n",
                kind="bug_pattern",
                symptom_description="segfault under load",
                root_cause_category="missing_synchronization_for_shared_state",
                fix_technique="atomic release/acquire",
                verification_method="TSan before/after",
                language="cpp",
            )
            return skill, None, None

        with mock.patch("aider.z.skills.cli.LocalSkillStore", return_value=store):
            with mock.patch("aider.z.skills.cli.upsert_skill_vector", return_value=True):
                with mock.patch("aider.z.skills.cli.sync_skill", return_value=None):
                    with mock.patch(
                        "aider.z.skills.cli.generate_skill", side_effect=fake_generate
                    ):
                        code = cmd_skill_create(
                            io,
                            "Fix intermittent segfault: missing sync on shared size field",
                            sync=False,
                        )
        self.assertEqual(code, 0)
        self.assertTrue(captured.get("prefer_bug_pattern"))
        skills = store.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].kind, "bug_pattern")
        self.assertEqual(
            skills[0].root_cause_category,
            "missing_synchronization_for_shared_state",
        )

    def test_create_feature_topic_stays_playbook_path(self):
        io = mock.MagicMock()
        captured = {}

        def fake_generate(topic, **kwargs):
            captured.update(kwargs)
            return (
                Skill(title="Rate limit", description="d", content="## Steps\n1\n"),
                None,
                None,
            )

        root = Path(tempfile.mkdtemp(prefix="z_skills_create_feat_"))
        store = LocalSkillStore(root=root)
        with mock.patch("aider.z.skills.cli.LocalSkillStore", return_value=store):
            with mock.patch("aider.z.skills.cli.upsert_skill_vector", return_value=True):
                with mock.patch("aider.z.skills.cli.sync_skill", return_value=None):
                    with mock.patch(
                        "aider.z.skills.cli.generate_skill", side_effect=fake_generate
                    ):
                        code = cmd_skill_create(
                            io,
                            "how this repo validates Stripe webhooks",
                            sync=False,
                        )
        self.assertEqual(code, 0)
        self.assertFalse(captured.get("prefer_bug_pattern"))


if __name__ == "__main__":
    unittest.main()

