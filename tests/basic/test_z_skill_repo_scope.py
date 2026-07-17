"""Skills must not auto-apply across projects (A → B contamination)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_skill_repo_")
os.environ["Z_HOME"] = _HOME

from aider.z.skills.cli import _stamp_repo_key, save_skill_from_task  # noqa: E402
from aider.z.skills.router import (  # noqa: E402
    collect_repo_signals,
    normalize_repo_key,
    route_skill,
    skill_matches_repo,
    task_is_bugfix_intent,
)
from aider.z.skills.schema import SKILL_KIND_BUG_PATTERN, Skill  # noqa: E402


class RepoKeyMatchTest(unittest.TestCase):
    def test_bound_skill_skips_other_repo(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            root_a = Path(a).resolve()
            root_b = Path(b).resolve()
            (root_a / "main.py").write_text("print('a')\n", encoding="utf-8")
            (root_b / "main.py").write_text("print('b')\n", encoding="utf-8")

            skill = Skill(
                title="Project A rate limiter",
                description="FlowGuard allow/prune for project A",
                content="## Steps\n1. Create flowguard/rate_limiter.py\n",
                kind="playbook",
                languages=["python"],
                quality_state="verified",
                repo_key=normalize_repo_key(root_a),
                source_files=["flowguard/rate_limiter.py"],
            )
            ok, reason = skill_matches_repo(skill, root_b)
            self.assertFalse(ok)
            self.assertIn("different project", reason)

            sig_b = collect_repo_signals(root_b)
            d = route_skill(skill, "add a rate limiter", sig_b, score=0.9)
            self.assertFalse(d.apply)
            self.assertIn("different project", d.reason)

    def test_bound_skill_applies_in_same_repo(self):
        with tempfile.TemporaryDirectory() as a:
            root_a = Path(a).resolve()
            (root_a / "main.py").write_text("print('a')\n", encoding="utf-8")
            skill = Skill(
                title="Project A helpers",
                description="helpers",
                content="## Steps\n1. Add util\n",
                kind="playbook",
                languages=["python"],
                quality_state="verified",
                repo_key=normalize_repo_key(root_a),
            )
            sig = collect_repo_signals(root_a)
            d = route_skill(skill, "add a helper", sig, score=0.9)
            self.assertTrue(d.apply)

    def test_shared_skill_applies_everywhere(self):
        with tempfile.TemporaryDirectory() as b:
            root_b = Path(b).resolve()
            (root_b / "main.py").write_text("x=1\n", encoding="utf-8")
            skill = Skill(
                title="Team convention",
                description="naming",
                content="Use snake_case",
                kind="playbook",
                languages=["python"],
                quality_state="verified",
                shared=True,
                repo_key="/some/other/project",
            )
            sig = collect_repo_signals(root_b)
            d = route_skill(skill, "rename helpers", sig, score=0.9)
            self.assertTrue(d.apply)

    def test_legacy_source_files_missing_skipped(self):
        """Older captures without repo_key still blocked when files aren't here."""
        with tempfile.TemporaryDirectory() as b:
            root_b = Path(b).resolve()
            (root_b / "main.py").write_text("x=1\n", encoding="utf-8")
            skill = Skill(
                title="Old FlowGuard capture",
                description="from project A",
                content="Create flowguard/rate_limiter.py",
                kind="playbook",
                languages=["python"],
                quality_state="verified",
                repo_key="",  # legacy
                source_files=["flowguard/rate_limiter.py", "tests/test_flowguard.py"],
            )
            ok, reason = skill_matches_repo(skill, root_b)
            self.assertFalse(ok)
            self.assertIn("foreign", reason)

    def test_stamp_repo_key_from_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            skill = Skill(title="t", description="d", content="c")
            _stamp_repo_key(skill, root=root)
            self.assertEqual(skill.repo_key, str(root))
            self.assertFalse(skill.shared)

    def test_stamp_bug_pattern_is_shared_portable(self):
        """Issue 3: bug_pattern defaults to shared=True / empty repo_key."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            skill = Skill(
                title="missing sync on size",
                description="segfault under concurrent resize",
                content="## Fix\n1. Atomicize size\n",
                kind=SKILL_KIND_BUG_PATTERN,
                symptom_description="intermittent segfault under concurrent resize",
            )
            _stamp_repo_key(skill, root=root)
            self.assertTrue(skill.shared)
            self.assertEqual(skill.repo_key, "")

    def test_bug_pattern_routes_in_different_project(self):
        """Cross-project retrieval: bug_pattern from A must apply in B."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            root_a = Path(a).resolve()
            root_b = Path(b).resolve()
            (root_a / "main.cpp").write_text("int main(){}\n", encoding="utf-8")
            (root_b / "main.cpp").write_text("int main(){}\n", encoding="utf-8")

            skill = Skill(
                title="Missing sync on shared size",
                description="segfault when size races with reader",
                content="## Fix\n1. Make size atomic\n",
                kind=SKILL_KIND_BUG_PATTERN,
                languages=["cpp"],
                quality_state="verified",
                symptom_description="intermittent segfault under concurrent resize",
                root_cause_category="missing_synchronization_for_shared_state",
            )
            _stamp_repo_key(skill, root=root_a)
            self.assertTrue(skill.shared)
            self.assertEqual(skill.repo_key, "")

            ok, reason = skill_matches_repo(skill, root_b)
            self.assertTrue(ok, reason)
            sig_b = collect_repo_signals(root_b)
            d = route_skill(
                skill,
                "fix intermittent segfault under concurrent resize",
                sig_b,
                score=0.9,
            )
            self.assertTrue(d.apply, d.reason)

    def test_save_skill_from_task_bug_pattern_is_shared(self):
        """Automatic capture path also stamps portable scope."""
        from aider.io import InputOutput

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            root_a = Path(a).resolve()
            root_b = Path(b).resolve()
            fake = Skill(
                title="Race on size field",
                description="crash under concurrent resize",
                content="## Fix\n1. Atomicize\n",
                kind=SKILL_KIND_BUG_PATTERN,
                symptom_description="segfault under concurrent resize",
                root_cause_category="missing_synchronization_for_shared_state",
            )

            class _Ground:
                ok = True
                reason = ""
                grounded_symbols = []

            io = InputOutput(pretty=False, yes=True)
            with mock.patch(
                "aider.z.skills.cli.generate_skill",
                return_value=(fake, None, _Ground()),
            ), mock.patch(
                "aider.z.skills.cli._persist_skill",
                side_effect=lambda io, skill, sync=True: skill,
            ):
                skill, created = save_skill_from_task(
                    io,
                    "fix intermittent segfault under concurrent resize",
                    repo_root=root_a,
                    prefer_bug_pattern=True,
                )
            self.assertTrue(created)
            self.assertIsNotNone(skill)
            self.assertEqual(skill.kind, SKILL_KIND_BUG_PATTERN)
            self.assertTrue(skill.shared)
            self.assertEqual(skill.repo_key, "")

            ok, reason = skill_matches_repo(skill, root_b)
            self.assertTrue(ok, reason)


class PathJailTest(unittest.TestCase):
    def test_outside_absolute_path_rejected(self):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            root_a = Path(a).resolve()
            root_b = Path(b).resolve()
            foreign = root_a / "secret.py"
            foreign.write_text("SECRET=1\n", encoding="utf-8")

            io = InputOutput(pretty=False, yes=True)
            # Minimal coder-like object with the methods under test
            class _C:
                root = str(root_b)
                abs_root_path_cache = {}
                abs_fnames = set()
                repo = None
                dry_run = True
                auto_commits = False
                warning_given = False

                abs_root_path = Coder.abs_root_path
                path_under_root = Coder.path_under_root
                allowed_to_edit = Coder.allowed_to_edit
                check_for_dirty_commit = lambda self, p: None
                check_added_files = lambda self: None

            c = _C()
            c.io = io
            self.assertIsNone(c.path_under_root(str(foreign)))
            # Escape must not resolve to project A
            resolved = c.abs_root_path(str(foreign))
            self.assertTrue(resolved.startswith(str(root_b)))
            with mock.patch.object(io, "tool_warning") as warn:
                allowed = c.allowed_to_edit(str(foreign))
            self.assertFalse(allowed)
            warn.assert_called()


if __name__ == "__main__":
    unittest.main()
