"""Tests for skill router: stack gate, scaffold satisfaction, multi-checkpoint."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_skill_router_")
os.environ["Z_HOME"] = _HOME

from aider.z.skills.infer import infer_kind, infer_languages, infer_metadata  # noqa: E402
from aider.z.skills.router import (  # noqa: E402
    artifacts_satisfied,
    collect_repo_signals,
    mark_skill_satisfied,
    route_skill,
    route_skills,
)
from aider.z.skills.schema import Skill  # noqa: E402
from aider.z.skills.session import (  # noqa: E402
    clear_session_skills,
    pull_skills_for_checkpoint,
)
from aider.z.skills import session as session_mod  # noqa: E402


class InferRouterFieldsTest(unittest.TestCase):
    def test_infer_scaffold_kind(self):
        self.assertEqual(
            infer_kind("Bootstrap a new module", title="Create Go HTTP server"),
            "scaffold",
        )
        self.assertEqual(
            infer_kind("Verify Stripe signatures", title="Stripe webhook validation"),
            "playbook",
        )

    def test_infer_languages(self):
        langs = infer_languages("use go.mod and net/http", title="Create Go project")
        self.assertIn("go", langs)

    def test_infer_metadata_sets_artifacts(self):
        meta = infer_metadata(
            "Scaffold a new Go module with main.go",
            title="Create Go HTTP server",
        )
        self.assertEqual(meta["kind"], "scaffold")
        self.assertIn("go", meta["languages"])
        self.assertTrue(meta["artifacts"])
        self.assertTrue(meta["apply_once"])


class RepoSignalsTest(unittest.TestCase):
    def test_detects_go_mod(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "go.mod").write_text("module example\n", encoding="utf-8")
            (root / "main.go").write_text("package main\n", encoding="utf-8")
            sig = collect_repo_signals(root)
            self.assertIn("go", sig.languages)
            self.assertTrue(sig.established)

    def test_artifacts_satisfied(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "go.mod").write_text("module x\n", encoding="utf-8")
            self.assertTrue(artifacts_satisfied(root, ["go.mod", "main.go"]))


class RouteSkillTest(unittest.TestCase):
    def test_skip_html_skill_on_go_repo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "go.mod").write_text("module x\n", encoding="utf-8")
            (root / "main.go").write_text("package main\n", encoding="utf-8")
            sig = collect_repo_signals(root)
            skill = Skill(
                title="Single HTML task manager",
                description="Build a todo UI in HTML/CSS",
                content="## Steps\n1. Create index.html\n",
                kind="playbook",
                languages=["html"],
                tags=["html", "css"],
                triggers=["html", "todo"],
            )
            d = route_skill(skill, "add a /health route in Go", sig, score=0.7)
            self.assertFalse(d.apply)
            self.assertIn("mismatch", d.reason)

    def test_skip_go_skill_on_python_repo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
            sig = collect_repo_signals(root)
            skill = Skill(
                title="Create Go HTTP server",
                description="Bootstrap a Go module",
                content="## Steps\n1. go mod init\n",
                kind="scaffold",
                languages=["go"],
                artifacts=["go.mod"],
            )
            d = route_skill(
                skill, "Build a Python CLI markdown scanner", sig, score=0.9
            )
            self.assertFalse(d.apply)
            self.assertIn("mismatch", d.reason)

    def test_skip_go_skill_on_python_task_even_in_empty_repo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sig = collect_repo_signals(root)
            skill = Skill(
                title="Create Go HTTP server",
                description="Bootstrap a Go module",
                content="go mod init example\n",
                kind="scaffold",
                languages=["go"],
            )
            d = route_skill(
                skill, "Create a Python CLI tool from scratch", sig, score=0.95
            )
            self.assertFalse(d.apply)
            self.assertIn("mismatch", d.reason)

    def test_skip_html_skill_on_python_task(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app.py").write_text("x=1\n", encoding="utf-8")
            sig = collect_repo_signals(root)
            skill = Skill(
                title="Single-file HTML task manager",
                description="Browser todo app",
                content="<html></html>",
                kind="playbook",
                languages=["html"],
            )
            d = route_skill(skill, "Python CLI task manager", sig, score=0.8)
            self.assertFalse(d.apply)

    def test_skip_scaffold_when_artifacts_exist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "go.mod").write_text("module x\n", encoding="utf-8")
            (root / "main.go").write_text("package main\n", encoding="utf-8")
            sig = collect_repo_signals(root)
            skill = Skill(
                id="scaffold-go-1",
                title="Create Go HTTP server",
                description="Bootstrap a Go project",
                content="## Steps\n1. go mod init\n",
                kind="scaffold",
                languages=["go"],
                artifacts=["go.mod", "main.go"],
                apply_once=True,
            )
            d = route_skill(
                skill, "add a /health route", sig, score=0.9
            )
            self.assertFalse(d.apply)
            self.assertTrue(
                "artifacts" in d.reason or "scaffold" in d.reason or "ongoing" in d.reason
            )

    def test_apply_scaffold_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sig = collect_repo_signals(root)
            skill = Skill(
                id="scaffold-go-2",
                title="Create Go HTTP server",
                description="Bootstrap a Go project",
                content="## Steps\n1. go mod init\n",
                kind="scaffold",
                languages=["go"],
                artifacts=["go.mod", "main.go"],
                apply_once=True,
            )
            d = route_skill(
                skill, "Create an HTTP server in Go from scratch", sig, score=0.9
            )
            self.assertTrue(d.apply)

    def test_skip_already_injected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sig = collect_repo_signals(root)
            skill = Skill(
                id="playbook-auth",
                title="Auth patterns",
                description="How we do auth",
                content="Use JWT.",
                kind="playbook",
                languages=["python"],
            )
            d = route_skill(
                skill,
                "fix login flow",
                sig,
                already_injected={"playbook-auth"},
                score=0.8,
            )
            self.assertFalse(d.apply)
            self.assertIn("already injected", d.reason)

    def test_route_skills_prefers_playbook_when_established(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "go.mod").write_text("module x\n", encoding="utf-8")
            (root / "main.go").write_text("package main\n", encoding="utf-8")
            scaffold = Skill(
                id="s1",
                title="Create Go project",
                description="bootstrap",
                content="init",
                kind="scaffold",
                languages=["go"],
                artifacts=["go.mod"],
                apply_once=True,
            )
            playbook = Skill(
                id="p1",
                title="Go HTTP handlers",
                description="handler conventions",
                content="Use chi router.",
                kind="playbook",
                languages=["go"],
            )
            inject, decisions = route_skills(
                "add /health handler",
                [(scaffold, 0.9), (playbook, 0.8)],
                root=root,
                already_injected=set(),
                limit=2,
            )
            # Scaffold should be skipped (artifacts exist); playbook applied
            ids = {s.id for s in inject}
            self.assertIn("p1", ids)
            self.assertNotIn("s1", ids)


class MultiCheckpointTest(unittest.TestCase):
    def setUp(self):
        clear_session_skills()

    def test_second_checkpoint_does_not_reinject(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = Skill(
                id="pb-1",
                title="Go HTTP handlers",
                description="handler conventions for go http",
                content="Use net/http patterns.",
                kind="playbook",
                languages=["go"],
                tags=["go", "http", "handler"],
                triggers=["handler", "http", "go"],
            )
            # Seed session index + injected tracking via pull mock path
            entry_skill = skill
            session_mod._SESSION_INDEX = [
                __import__("aider.z.skills.schema", fromlist=["SkillIndexEntry"]).SkillIndexEntry(
                    id=skill.id,
                    title=skill.title,
                    description=skill.description,
                    kind="playbook",
                    languages=["go"],
                    tags=skill.tags,
                    triggers=skill.triggers,
                )
            ]

            with mock.patch(
                "aider.z.skills.session.resolve_full_skill", return_value=entry_skill
            ), mock.patch(
                "aider.z.skills.session.retrieve_skill_candidates",
                return_value=[(entry_skill, 0.85)],
            ):
                first, _ = pull_skills_for_checkpoint(
                    "add go http handler", root=root, limit=2, checkpoint="turn"
                )
                self.assertEqual(len(first), 1)
                second, reasons = pull_skills_for_checkpoint(
                    "now add tests for the handler",
                    root=root,
                    limit=2,
                    checkpoint="reflect",
                )
                self.assertEqual(len(second), 0)
                self.assertTrue(any("already injected" in r for r in reasons))

    def test_satisfaction_state_persists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mark_skill_satisfied(root, "scaffold-x")
            from aider.z.skills.router import is_skill_satisfied

            self.assertTrue(is_skill_satisfied(root, "scaffold-x"))


if __name__ == "__main__":
    unittest.main()
