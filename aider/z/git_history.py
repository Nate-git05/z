"""Local git history for the Z Editor Commit Gate "Pushed" tab.

No auth, no network — plain GitPython reads against the workspace repo.
Kept separate from ``aider.repo.GitRepo`` (the agent's commit-writing path)
since this module only ever reads.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import git


def _open_repo(repo_root: str) -> git.Repo:
    return git.Repo(repo_root, search_parent_directories=True)


def list_commits(repo_root: str, limit: int = 30) -> list[dict[str, Any]]:
    repo = _open_repo(repo_root)
    out: list[dict[str, Any]] = []
    for commit in repo.iter_commits(max_count=max(1, min(limit, 200))):
        stats = commit.stats.total
        message = str(commit.message or "").strip()
        summary = message.splitlines()[0] if message else "(no message)"
        out.append(
            {
                "sha": commit.hexsha,
                "shortSha": commit.hexsha[:7],
                "summary": summary,
                "message": message,
                "author": commit.author.name or commit.author.email or "unknown",
                "authoredAt": commit.authored_datetime.isoformat(),
                "insertions": int(stats.get("insertions", 0)),
                "deletions": int(stats.get("deletions", 0)),
                "filesChanged": int(stats.get("files", 0)),
            }
        )
    return out


def show_commit(repo_root: str, sha: str) -> dict[str, Any]:
    repo = _open_repo(repo_root)
    commit = repo.commit(sha)
    stats = commit.stats.total
    message = str(commit.message or "").strip()
    summary = message.splitlines()[0] if message else "(no message)"
    diff = repo.git.show(commit.hexsha, "--no-color", "-p", "--stat")
    return {
        "sha": commit.hexsha,
        "shortSha": commit.hexsha[:7],
        "summary": summary,
        "message": message,
        "author": commit.author.name or commit.author.email or "unknown",
        "authoredAt": commit.authored_datetime.isoformat(),
        "insertions": int(stats.get("insertions", 0)),
        "deletions": int(stats.get("deletions", 0)),
        "filesChanged": int(stats.get("files", 0)),
        "diff": diff,
    }


_GITHUB_REMOTE_RE = re.compile(
    r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$"
)


def detect_github_remote(repo_root: str) -> Optional[tuple[str, str]]:
    try:
        repo = _open_repo(repo_root)
        origin = repo.remotes.origin
        for url in origin.urls:
            m = _GITHUB_REMOTE_RE.search(url)
            if m:
                return m.group(1), m.group(2)
    except Exception:
        return None
    return None
