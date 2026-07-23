"""Skill injection safety: mode-gating, greenfield language compatibility,
and the weak-model relevance filter on top of retrieval.

A plain self-introduction ("hello my name is nathaniel") used to still pull
and inject an unrelated skill as a literal "directive" the model was told to
follow — this file covers the three independent layers that now prevent
that: (1) skill pulling is gated on TaskMode, not the narrower casual-chat
regex; (2) a greenfield repo with no inferred task language no longer lets a
language-specific scaffold skill through unchallenged; (3) even skills that
pass both of those get a final weak-model relevance check, which fails open
(keeps today's result) on any failure so a broken model call can never
silently drop skills that already passed real retrieval.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_HOME = tempfile.mkdtemp(prefix="z_skill_gate_")
os.environ["Z_HOME"] = _HOME
os.environ.pop("Z_SKILL_RELEVANCE_CLASSIFY", None)
os.environ.pop("Z_SKILL_RELEVANCE_TIMEOUT", None)

from aider.z.skills.router import (  # noqa: E402
    RepoSignals,
    filter_skills_by_relevance,
    language_compatible,
)
from aider.z.skills.schema import Skill  # noqa: E402


def _make_skill(**kwargs) -> Skill:
    defaults = dict(title="Some skill", description="Does something", content="")
    defaults.update(kwargs)
    return Skill(**defaults)


class _FakeClassifierModel:
    def __init__(self, response=None, *, raise_exc=None, hang=False):
        self.response = response
        self.raise_exc = raise_exc
        self.hang = hang
        self.calls = []

    def simple_send_with_retries(self, messages):
        self.calls.append(messages)
        if self.hang:
            time.sleep(30)
        if self.raise_exc:
            raise self.raise_exc
        return self.response


class LanguageCompatibleGreenfieldTests(unittest.TestCase):
    def test_language_tagged_skill_rejected_with_no_task_signal(self):
        skill = _make_skill(languages=["go"])
        signals = RepoSignals(root=Path("."), languages=set(), established=False)
        self.assertFalse(language_compatible(skill, signals, task="hello my name is nathaniel"))

    def test_language_less_skill_still_allowed(self):
        skill = _make_skill(languages=[])
        signals = RepoSignals(root=Path("."), languages=set(), established=False)
        self.assertTrue(language_compatible(skill, signals, task="hello my name is nathaniel"))

    def test_matching_task_language_still_allowed(self):
        skill = _make_skill(languages=["rust"])
        signals = RepoSignals(root=Path("."), languages=set(), established=False)
        self.assertTrue(
            language_compatible(skill, signals, task="build an http server in rust")
        )


class SkillRelevanceFilterTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_SKILL_RELEVANCE_CLASSIFY", None)
        os.environ.pop("Z_SKILL_RELEVANCE_TIMEOUT", None)

    def test_no_classifier_model_keeps_skills_unchanged(self):
        skills = [_make_skill(title="A"), _make_skill(title="B")]
        out = filter_skills_by_relevance(skills, "some message", None)
        self.assertEqual(out, skills)

    def test_model_narrows_to_relevant_skill(self):
        skills = [_make_skill(title="Go module init"), _make_skill(title="Docstrings")]
        fake = _FakeClassifierModel(response="2")
        out = filter_skills_by_relevance(skills, "add docstrings to this file", fake)
        self.assertEqual([s.title for s in out], ["Docstrings"])
        self.assertEqual(len(fake.calls), 1)

    def test_model_says_none_drops_all(self):
        skills = [_make_skill(title="Go module init")]
        fake = _FakeClassifierModel(response="none")
        out = filter_skills_by_relevance(skills, "hello my name is nathaniel", fake)
        self.assertEqual(out, [])

    def test_timeout_fails_open(self):
        os.environ["Z_SKILL_RELEVANCE_TIMEOUT"] = "0.2"
        skills = [_make_skill(title="A")]
        fake = _FakeClassifierModel(hang=True)
        out = filter_skills_by_relevance(skills, "some message", fake)
        self.assertEqual(out, skills)

    def test_exception_fails_open(self):
        skills = [_make_skill(title="A")]
        fake = _FakeClassifierModel(raise_exc=RuntimeError("boom"))
        out = filter_skills_by_relevance(skills, "some message", fake)
        self.assertEqual(out, skills)

    def test_unparseable_response_fails_open(self):
        skills = [_make_skill(title="A")]
        fake = _FakeClassifierModel(response="I'm not sure, maybe?")
        out = filter_skills_by_relevance(skills, "some message", fake)
        self.assertEqual(out, skills)

    def test_escape_hatch_skips_the_model_call_entirely(self):
        os.environ["Z_SKILL_RELEVANCE_CLASSIFY"] = "0"
        skills = [_make_skill(title="A")]
        fake = _FakeClassifierModel(response="none")
        out = filter_skills_by_relevance(skills, "some message", fake)
        self.assertEqual(out, skills)
        self.assertEqual(fake.calls, [])


class SkillGateModeTests(unittest.TestCase):
    """Confirms the run_one() call-site gating: skills are only pulled for
    modes that actually decompose requirements, not every non-casual-chat
    message."""

    def _make_coder(self):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput
        from aider.models import Model

        io = InputOutput(yes=True)
        coder = Coder.create(
            main_model=Model("gpt-4o-mini"), io=io, fnames=[], edit_format="diff"
        )
        coder.root = tempfile.mkdtemp(prefix="z_skillgate_root_")
        coder.repo = None
        coder.abs_fnames = set()

        def fake_send(*args, **kwargs):
            coder.partial_response_content = "Just a plain reply, no edits."
            coder.partial_response_function_call = dict()
            return []

        coder.send = fake_send
        return coder

    def test_ask_mode_skips_skill_pull(self):
        coder = self._make_coder()
        with patch.object(coder, "_maybe_pull_skills") as mock_pull:
            coder.run(with_message="hello my name is nathaniel")
        mock_pull.assert_not_called()

    def test_implement_mode_pulls_skills(self):
        coder = self._make_coder()
        with patch.object(coder, "_maybe_pull_skills") as mock_pull:
            coder.run(with_message="create a hello.py file that prints hello")
        mock_pull.assert_called()


if __name__ == "__main__":
    unittest.main()
