"""Skills must not auto-apply across projects (A → B contamination)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_skill_repo_")
os.environ["Z_HOME"] = _HOME

from aider.z.skills.cli import _stamp_repo_key  # noqa: E402
from aider.z.skills.router import (  # noqa: E402
    collect_repo_signals,
    normalize_repo_key,
    route_skill,
    skill_matches_repo,
)
from aider.z.skills.schema import Skill  # noqa: E402


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
