"""GitHub PR integration for the Z Editor Commit Gate "Pull Requests" tab.

Reuses the GitHub connection already wired for MCP (local PAT/OAuth secret,
falling back to the cloud-synced credential for signed-in accounts) — no new
connect flow. If nothing is connected, callers should point the user at the
existing MCP panel's ``mcp/oauthStart`` GitHub connect path.
"""

from __future__ import annotations

from typing import Any, Optional

import requests

_API = "https://api.github.com"
_TIMEOUT = 15


def get_github_token() -> Optional[str]:
    """Local MCP connection secret first, then cloud-synced OAuth credential."""
    try:
        from aider.z import mcp_local

        for conn in mcp_local.list_connections(enabled_only=True):
            name = conn.get("serverName") or conn.get("server_name") or ""
            if name != "github":
                continue
            secrets = mcp_local._load_secrets().get(conn.get("id", "")) or {}
            token = secrets.get("access_token") or secrets.get("token")
            if token:
                return str(token)
    except Exception:
        pass

    try:
        from aider.z.mcp_client import fetch_mcp_runtime

        for tool in fetch_mcp_runtime():
            if tool.server_name != "github":
                continue
            creds = tool.credentials or {}
            token = creds.get("access_token") or creds.get("token")
            if token:
                return str(token)
    except Exception:
        pass
    return None


def _headers(token: str, *, accept: Optional[str] = None) -> dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept": accept or "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get(url: str, token: str, *, accept: Optional[str] = None) -> requests.Response:
    resp = requests.get(url, headers=_headers(token, accept=accept), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp


def _current_login(token: str) -> Optional[str]:
    try:
        return _get(f"{_API}/user", token).json().get("login")
    except Exception:
        return None


def list_pull_requests(token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    prs = _get(f"{_API}/repos/{owner}/{repo}/pulls?state=all&per_page=30", token).json()
    me = _current_login(token)
    out: list[dict[str, Any]] = []
    for pr in prs:
        user = (pr.get("user") or {}).get("login")
        reviewers = [r.get("login") for r in (pr.get("requested_reviewers") or [])]
        out.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "state": pr.get("state"),
                "draft": bool(pr.get("draft")),
                "author": user,
                "mine": bool(me) and user == me,
                "reviewRequested": bool(me) and me in reviewers,
                "branch": (pr.get("head") or {}).get("ref"),
                "baseBranch": (pr.get("base") or {}).get("ref"),
                "createdAt": pr.get("created_at"),
                "updatedAt": pr.get("updated_at"),
                "htmlUrl": pr.get("html_url"),
            }
        )
    return out


def get_pull_request(token: str, owner: str, repo: str, number: int) -> dict[str, Any]:
    pr = _get(f"{_API}/repos/{owner}/{repo}/pulls/{number}", token).json()
    sha = (pr.get("head") or {}).get("sha")

    checks: list[dict[str, Any]] = []
    if sha:
        try:
            data = _get(f"{_API}/repos/{owner}/{repo}/commits/{sha}/check-runs", token).json()
            for run in data.get("check_runs") or []:
                checks.append(
                    {
                        "name": run.get("name"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                    }
                )
        except Exception:
            pass

    comments: list[dict[str, Any]] = []
    try:
        data = _get(f"{_API}/repos/{owner}/{repo}/issues/{number}/comments", token).json()
        for c in data:
            comments.append(
                {
                    "author": (c.get("user") or {}).get("login"),
                    "body": c.get("body"),
                    "createdAt": c.get("created_at"),
                    "htmlUrl": c.get("html_url"),
                }
            )
    except Exception:
        pass

    diff = ""
    try:
        diff = _get(
            f"{_API}/repos/{owner}/{repo}/pulls/{number}",
            token,
            accept="application/vnd.github.v3.diff",
        ).text
    except Exception:
        pass

    reviewers = [r.get("login") for r in (pr.get("requested_reviewers") or [])]
    return {
        "pr": {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "body": pr.get("body") or "",
            "state": pr.get("state"),
            "draft": bool(pr.get("draft")),
            "author": (pr.get("user") or {}).get("login"),
            "branch": (pr.get("head") or {}).get("ref"),
            "baseBranch": (pr.get("base") or {}).get("ref"),
            "reviewers": reviewers,
            "comments": pr.get("comments", 0),
            "additions": pr.get("additions", 0),
            "deletions": pr.get("deletions", 0),
            "changedFiles": pr.get("changed_files", 0),
            "createdAt": pr.get("created_at"),
            "updatedAt": pr.get("updated_at"),
            "htmlUrl": pr.get("html_url"),
            "mergeable": pr.get("mergeable"),
        },
        "checks": checks,
        "comments": comments,
        "diff": diff,
    }
