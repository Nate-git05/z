"""Tests for workspace CLI, credentials organization field, and sync scoping."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aider.z.cli import cmd_workspace
from aider.z.credentials import Credentials, UserProfile, WorkspaceContext
from aider.z.uncertainty.engine import attach_engine_to_coder
from aider.z.uncertainty.schema import NodeType, Tier, UncertaintyNode
from aider.z.workspace_cli import WorkspaceError, invite_member


class TestWorkspaceContextOrganization(unittest.TestCase):
    def test_workspace_context_organization_field_round_trips(self):
        creds = Credentials(
            access_token="tok",
            workspace=WorkspaceContext(
                id="ws1",
                name="Acme",
                role="owner",
                organization="Acme Corp",
            ),
        )
        data = creds.to_dict()
        restored = Credentials.from_dict(data)
        self.assertEqual(restored.workspace.organization, "Acme Corp")
        self.assertEqual(restored.workspace.id, "ws1")
        self.assertEqual(restored.workspace.name, "Acme")
        self.assertEqual(restored.workspace.role, "owner")

    def test_from_dict_without_organization_stays_none(self):
        restored = Credentials.from_dict(
            {
                "access_token": "tok",
                "workspace": {"id": "ws1", "name": "Acme", "role": "owner"},
            }
        )
        self.assertIsNone(restored.workspace.organization)


class TestWorkspaceIdThreading(unittest.TestCase):
    def test_workspace_id_threaded_through_remote_sync(self):
        """Regression: attach_engine_to_coder must pass creds.workspace.id."""
        root = Path(tempfile.mkdtemp())
        coder = SimpleNamespace(root=root)
        creds = Credentials(
            access_token="tok",
            workspace=WorkspaceContext(id="ws-scoped", name="Team", role="owner"),
        )
        sync_calls = []
        fetch_calls = []

        def fake_sync(node, *, repo_key, workspace_id=None):
            sync_calls.append(
                {"repo_key": repo_key, "workspace_id": workspace_id, "node": node}
            )
            return True

        def fake_fetch(*, repo_key, workspace_id=None):
            fetch_calls.append({"repo_key": repo_key, "workspace_id": workspace_id})
            return []

        with patch("aider.z.auth.current_session", return_value=creds), patch(
            "aider.z.uncertainty.remote.sync_node", side_effect=fake_sync
        ), patch(
            "aider.z.uncertainty.remote.fetch_workspace_nodes", side_effect=fake_fetch
        ), patch(
            "aider.z.uncertainty.store.UncertaintyStore.load_local"
        ), patch(
            "aider.z.uncertainty.store.UncertaintyStore.save_local"
        ):
            engine = attach_engine_to_coder(coder, user_label="tester")
            node = UncertaintyNode(
                id="n1",
                title="t",
                type=NodeType.MISSING_TEST,
                confidence_tier=Tier.LOW,
                risk_tier=Tier.MEDIUM,
                summary="s",
            )
            engine.ctx.store.add(node)

        self.assertEqual(len(fetch_calls), 1)
        self.assertEqual(fetch_calls[0]["workspace_id"], "ws-scoped")
        self.assertEqual(len(sync_calls), 1)
        self.assertEqual(sync_calls[0]["workspace_id"], "ws-scoped")
        self.assertEqual(sync_calls[0]["repo_key"], str(root))


class TestWorkspaceCli(unittest.TestCase):
    def _args(self, **kwargs):
        base = {
            "workspace_command": None,
            "name": [],
            "organization": None,
            "identifier": [],
        }
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_create_workspace_persists_to_current_session(self):
        io = MagicMock()
        creds = Credentials(
            access_token="tok",
            user=UserProfile(email="a@b.com", name="Ada"),
            workspace=WorkspaceContext(id="old", name="Old", role="member"),
            expires_at=9_999_999_999,
        )
        saved = []

        def fake_save(c, path=None):
            saved.append(c)
            return Path("/tmp/unused")

        with patch("aider.z.auth.current_session", return_value=creds), patch(
            "aider.z.workspace_cli.create_workspace",
            return_value={
                "id": "ws-new",
                "name": "Acme Team",
                "organization": "Acme Corp",
                "role": "owner",
            },
        ), patch(
            "aider.z.credentials.save_credentials", side_effect=fake_save
        ):
            rc = cmd_workspace(
                io,
                self._args(
                    workspace_command="create",
                    name=["Acme", "Team"],
                    organization="Acme Corp",
                ),
            )

        self.assertEqual(rc, 0)
        self.assertEqual(creds.workspace.id, "ws-new")
        self.assertEqual(creds.workspace.name, "Acme Team")
        self.assertEqual(creds.workspace.organization, "Acme Corp")
        self.assertEqual(creds.workspace.role, "owner")
        self.assertEqual(len(saved), 1)
        io.tool_output.assert_called()

    def test_invite_requires_active_workspace(self):
        io = MagicMock()
        creds = Credentials(
            access_token="tok",
            workspace=WorkspaceContext(),  # no id
            expires_at=9_999_999_999,
        )
        with patch("aider.z.auth.current_session", return_value=creds), patch(
            "aider.z.workspace_cli.invite_member"
        ) as invite_mock:
            rc = cmd_workspace(
                io,
                self._args(
                    workspace_command="invite",
                    identifier=["person@example.com"],
                ),
            )
        self.assertEqual(rc, 1)
        invite_mock.assert_not_called()
        io.tool_error.assert_called()
        self.assertIn("No active workspace", io.tool_error.call_args[0][0])

    def test_invite_member_helper_rejects_unauthenticated(self):
        with patch("aider.z.workspace_cli.current_session", return_value=None):
            with self.assertRaises(WorkspaceError) as ctx:
                invite_member("ws1", "a@b.com")
        self.assertIn("Not signed in", str(ctx.exception))

    def test_switch_is_explicitly_unavailable(self):
        io = MagicMock()
        rc = cmd_workspace(io, self._args(workspace_command="switch"))
        self.assertEqual(rc, 1)
        self.assertIn("not available", io.tool_error.call_args[0][0].lower())


if __name__ == "__main__":
    unittest.main()
