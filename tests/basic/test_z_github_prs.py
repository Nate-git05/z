"""Commit Gate 'Pull Requests' tab — GitHub REST integration (mocked HTTP)."""

from __future__ import annotations

import unittest
from unittest import mock

from aider.z import github_prs


def _resp(json_data=None, text_data="", status=200):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text_data
    resp.raise_for_status = mock.Mock()
    return resp


class GetGithubTokenTest(unittest.TestCase):
    def test_prefers_local_mcp_secret(self):
        with mock.patch("aider.z.mcp_local.list_connections") as list_conns, mock.patch(
            "aider.z.mcp_local._load_secrets"
        ) as load_secrets:
            list_conns.return_value = [{"id": "c1", "serverName": "github"}]
            load_secrets.return_value = {"c1": {"token": "pat-123"}}
            self.assertEqual(github_prs.get_github_token(), "pat-123")

    def test_falls_back_to_cloud_runtime(self):
        with mock.patch("aider.z.mcp_local.list_connections", return_value=[]), mock.patch(
            "aider.z.mcp_client.fetch_mcp_runtime"
        ) as fetch_runtime:
            tool = mock.Mock(server_name="github", credentials={"access_token": "oauth-456"})
            fetch_runtime.return_value = [tool]
            self.assertEqual(github_prs.get_github_token(), "oauth-456")

    def test_none_when_nothing_connected(self):
        with mock.patch("aider.z.mcp_local.list_connections", return_value=[]), mock.patch(
            "aider.z.mcp_client.fetch_mcp_runtime", return_value=[]
        ):
            self.assertIsNone(github_prs.get_github_token())


class ListPullRequestsTest(unittest.TestCase):
    def test_tags_mine_and_review_requested(self):
        pulls = [
            {
                "number": 5,
                "title": "feat(ui): soft palette",
                "state": "open",
                "draft": True,
                "user": {"login": "nmat0556"},
                "requested_reviewers": [],
                "head": {"ref": "cursor/z-soft-ui-313a"},
                "base": {"ref": "main"},
                "created_at": "2026-07-21T14:00:00Z",
                "updated_at": "2026-07-21T15:00:00Z",
                "html_url": "https://github.com/o/r/pull/5",
            },
            {
                "number": 6,
                "title": "fix: something else",
                "state": "open",
                "draft": False,
                "user": {"login": "someone-else"},
                "requested_reviewers": [{"login": "nmat0556"}],
                "head": {"ref": "fix-branch"},
                "base": {"ref": "main"},
                "created_at": "2026-07-20T14:00:00Z",
                "updated_at": "2026-07-20T15:00:00Z",
                "html_url": "https://github.com/o/r/pull/6",
            },
        ]
        with mock.patch("aider.z.github_prs.requests.get") as get:
            get.side_effect = [
                _resp(json_data=pulls),
                _resp(json_data={"login": "nmat0556"}),
            ]
            prs = github_prs.list_pull_requests("tok", "o", "r")

        self.assertEqual(len(prs), 2)
        self.assertTrue(prs[0]["mine"])
        self.assertFalse(prs[0]["reviewRequested"])
        self.assertFalse(prs[1]["mine"])
        self.assertTrue(prs[1]["reviewRequested"])


class GetPullRequestTest(unittest.TestCase):
    def test_bundles_detail_checks_comments_diff(self):
        pr_detail = {
            "number": 5,
            "title": "feat(ui): soft palette",
            "body": "Summary here",
            "state": "open",
            "draft": True,
            "user": {"login": "nmat0556"},
            "head": {"ref": "cursor/z-soft-ui-313a", "sha": "abc123"},
            "base": {"ref": "main"},
            "requested_reviewers": [],
            "comments": 2,
            "additions": 300,
            "deletions": 201,
            "changed_files": 11,
            "created_at": "2026-07-21T14:00:00Z",
            "updated_at": "2026-07-21T15:00:00Z",
            "html_url": "https://github.com/o/r/pull/5",
            "mergeable": True,
        }
        checks = {"check_runs": [{"name": "build (3.12)", "status": "completed", "conclusion": "failure"}]}
        comments = [
            {
                "user": {"login": "vercel"},
                "body": "Deployment failed",
                "created_at": "2026-07-21T18:37:00Z",
                "html_url": "https://github.com/o/r/pull/5#comment-1",
            }
        ]
        diff_text = "diff --git a/x b/x\n+added\n"

        with mock.patch("aider.z.github_prs.requests.get") as get:
            get.side_effect = [
                _resp(json_data=pr_detail),
                _resp(json_data=checks),
                _resp(json_data=comments),
                _resp(text_data=diff_text),
            ]
            result = github_prs.get_pull_request("tok", "o", "r", 5)

        self.assertEqual(result["pr"]["number"], 5)
        self.assertEqual(result["pr"]["additions"], 300)
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["conclusion"], "failure")
        self.assertEqual(len(result["comments"]), 1)
        self.assertEqual(result["comments"][0]["author"], "vercel")
        self.assertEqual(result["diff"], diff_text)

    def test_diff_failure_does_not_break_the_bundle(self):
        pr_detail = {
            "number": 5,
            "title": "t",
            "user": {"login": "u"},
            "head": {"ref": "b", "sha": "abc"},
            "base": {"ref": "main"},
            "requested_reviewers": [],
        }
        with mock.patch("aider.z.github_prs.requests.get") as get:
            def side_effect(url, headers=None, timeout=None):
                if "check-runs" in url:
                    return _resp(json_data={"check_runs": []})
                if "/comments" in url:
                    return _resp(json_data=[])
                if headers and headers.get("Accept") == "application/vnd.github.v3.diff":
                    raise github_prs.requests.RequestException("boom")
                return _resp(json_data=pr_detail)

            get.side_effect = side_effect
            result = github_prs.get_pull_request("tok", "o", "r", 5)

        self.assertEqual(result["diff"], "")
        self.assertEqual(result["pr"]["number"], 5)


if __name__ == "__main__":
    unittest.main()
