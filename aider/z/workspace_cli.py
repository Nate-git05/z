"""Workspace create/invite/members — thin client for the same backend
z_server already exposes for uncertainty-node sync (see z/uncertainty/remote.py)."""

from __future__ import annotations

from typing import Optional

import requests

from .auth import current_session, get_auth_base_url


class WorkspaceError(Exception):
    pass


def _auth_headers() -> dict:
    creds = current_session()
    if not creds or not creds.is_authenticated():
        raise WorkspaceError("Not signed in — run `z login` first.")
    return {"Authorization": f"Bearer {creds.access_token}"}


def create_workspace(name: str, *, organization: Optional[str] = None) -> dict:
    base = get_auth_base_url()
    resp = requests.post(
        f"{base}/v1/workspaces",
        headers=_auth_headers(),
        json={"name": name, "organization": organization},
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise WorkspaceError(
            f"Could not create workspace: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


def invite_member(workspace_id: str, identifier: str) -> dict:
    """identifier: an email address or a phone number — the backend
    determines which by format, same ambiguity the existing login screen
    already resolves for email vs phone sign-in."""
    base = get_auth_base_url()
    resp = requests.post(
        f"{base}/v1/workspaces/{workspace_id}/invite",
        headers=_auth_headers(),
        json={"identifier": identifier},
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise WorkspaceError(
            f"Could not send invite: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


def list_members(workspace_id: str) -> list[dict]:
    base = get_auth_base_url()
    resp = requests.get(
        f"{base}/v1/workspaces/{workspace_id}/members",
        headers=_auth_headers(),
        timeout=15,
    )
    if resp.status_code != 200:
        raise WorkspaceError(f"Could not list members: {resp.status_code}")
    return list(resp.json().get("members") or [])
