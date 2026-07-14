"""Tests for Z skills — local store, relevance, API, web page."""

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

from aider.z.skills.index import match_skills, relevance_score  # noqa: E402
from aider.z.skills.schema import Skill, SkillIndexEntry  # noqa: E402
from aider.z.skills.session import (  # noqa: E402
    format_skills_for_context,
    load_skills_for_session,
    select_relevant_skills,
)
from aider.z.skills.store import LocalSkillStore, skill_from_markdown, skill_to_markdown  # noqa: E402
from z_server.app import create_app  # noqa: E402
from z_server.db import init_db, reset_engine  # noqa: E402


class LocalStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="z_skills_local_"))
        self.store = LocalSkillStore(root=self.root)

    def test_save_and_list(self):
        skill = Skill(
            title="Stripe webhook validation",
            description="How this repo validates Stripe webhooks",
            content="## Steps\n1. Verify signature\n2. Handle idempotency\n",
        )
        path = self.store.save(skill)
        self.assertTrue(path.is_file())
        listed = self.store.list_skills()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].title, "Stripe webhook validation")
        loaded = self.store.get(skill.id)
        self.assertIsNotNone(loaded)
        self.assertIn("Verify signature", loaded.content)

    def test_roundtrip_markdown(self):
        skill = Skill(
            title="Migration scripts",
            description="Team convention for Alembic migrations",
            content="Always expand then contract.",
            created_by="Ada",
        )
        text = skill_to_markdown(skill)
        again = skill_from_markdown(text, filename="x.md")
        self.assertEqual(again.title, skill.title)
        self.assertEqual(again.description, skill.description)
        self.assertIn("expand then contract", again.content)

    def test_index_is_lightweight(self):
        self.store.save(
            Skill(title="A", description="desc A", content="long body " * 50)
        )
        idx = self.store.index()
        self.assertEqual(len(idx), 1)
        self.assertNotIn("content", idx[0])
        self.assertEqual(idx[0]["title"], "A")


class RelevanceTest(unittest.TestCase):
    def test_keyword_match(self):
        entry = SkillIndexEntry(
            id="1",
            title="Stripe webhook validation",
            description="Verify Stripe webhook signatures and idempotency",
        )
        score = relevance_score("add stripe webhook handler with signature check", entry)
        self.assertGreaterEqual(score, 0.35)
        misses = relevance_score("refactor css layout for homepage", entry)
        self.assertLess(misses, 0.35)

    def test_match_skills_limit(self):
        index = [
            SkillIndexEntry(id="1", title="Stripe webhooks", description="payment hooks"),
            SkillIndexEntry(id="2", title="CSS layout", description="homepage styles"),
            SkillIndexEntry(id="3", title="Alembic migrations", description="schema changes"),
        ]
        hits = match_skills("fix stripe webhook signature", index, threshold=0.2)
        self.assertTrue(hits)
        self.assertEqual(hits[0][0].id, "1")


class SessionPullTest(unittest.TestCase):
    def test_select_and_format(self):
        root = Path(tempfile.mkdtemp(prefix="z_skills_sess_"))
        store = LocalSkillStore(root=root)
        skill = Skill(
            title="Stripe webhook validation",
            description="Validate Stripe webhooks in this repo",
            content="Check the Stripe-Signature header.",
        )
        store.save(skill)

        with mock.patch("aider.z.skills.session.LocalSkillStore", return_value=store):
            with mock.patch("aider.z.skills.session.fetch_skill_index", return_value=[]):
                load_skills_for_session()
                pulled = select_relevant_skills(
                    "implement stripe webhook validation endpoint"
                )
        self.assertEqual(len(pulled), 1)
        block = format_skills_for_context(pulled)
        self.assertIn("Stripe webhook", block)
        self.assertIn("Stripe-Signature", block)


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
        self.assertNotIn("content", rows[0])  # index is lightweight

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
        # Use cookie session from login flow — hit page with bearer via cookie
        # TestClient keeps cookies from verify? Auth was via API not cookie.
        # Set cookie manually from token
        token = self.headers["Authorization"].split(" ", 1)[1]
        self.client.cookies.set("z_session", token)
        page = self.client.get("/app/skills")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Migration convention", page.text)
        self.assertIn("z skill create", page.text)


class GenerateParseTest(unittest.TestCase):
    def test_generate_uses_model_json(self):
        from aider.z.skills.generate import generate_skill

        fake_response = (
            '{"title": "T", "description": "D", "content": "## Body\\nDo the thing."}'
        )

        class FakeModel:
            def simple_send_with_retries(self, messages):
                return fake_response

        with mock.patch("aider.z.skills.generate.resolve_model", return_value=FakeModel()):
            skill, err = generate_skill("how we do the thing")
        self.assertIsNone(err)
        self.assertEqual(skill.title, "T")
        self.assertIn("Do the thing", skill.content)


if __name__ == "__main__":
    unittest.main()
