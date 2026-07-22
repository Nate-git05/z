"""Commit Gate 'Pushed' tab — local git history (no auth, no network)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import git

from aider.z.git_history import detect_github_remote, list_commits, show_commit


class GitHistoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.repo = git.Repo.init(self.root)
        with self.repo.config_writer() as cw:
            cw.set_value("user", "name", "Test User")
            cw.set_value("user", "email", "test@example.com")

        (Path(self.root) / "a.txt").write_text("one\n")
        self.repo.index.add(["a.txt"])
        self.first_sha = self.repo.index.commit("first commit").hexsha

        (Path(self.root) / "a.txt").write_text("one\ntwo\n")
        self.repo.index.add(["a.txt"])
        self.second_sha = self.repo.index.commit("second commit\n\nmore detail").hexsha

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_commits_newest_first(self):
        commits = list_commits(self.root)
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0]["sha"], self.second_sha)
        self.assertEqual(commits[0]["summary"], "second commit")
        self.assertEqual(commits[1]["sha"], self.first_sha)
        self.assertEqual(commits[0]["insertions"], 1)

    def test_list_commits_respects_limit(self):
        commits = list_commits(self.root, limit=1)
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]["sha"], self.second_sha)

    def test_show_commit_includes_diff(self):
        result = show_commit(self.root, self.second_sha)
        self.assertEqual(result["sha"], self.second_sha)
        self.assertIn("diff", result)
        self.assertIn("+two", result["diff"])

    def test_detect_github_remote_none_by_default(self):
        self.assertIsNone(detect_github_remote(self.root))

    def test_detect_github_remote_parses_https_url(self):
        self.repo.create_remote("origin", "https://github.com/Nate-git05/z.git")
        self.assertEqual(detect_github_remote(self.root), ("Nate-git05", "z"))

    def test_detect_github_remote_parses_ssh_url(self):
        self.repo.create_remote("origin", "git@github.com:Nate-git05/z.git")
        self.assertEqual(detect_github_remote(self.root), ("Nate-git05", "z"))


if __name__ == "__main__":
    unittest.main()
