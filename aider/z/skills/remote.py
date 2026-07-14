"""Remote sync of skills to z_server (optional, when signed in)."""

from __future__ import annotations

import os
from typing import Any, List, Optional

import requests

from aider.z.auth import current_session, get_auth_base_url

from .schema import Skill


def _token() -> Optional[str]:
    creds = current_session()
    return (creds.access_token if creds else None) or os.environ.get("Z_ACCESS_TOKEN")


def fetch_skill_index(*, workspace_id: Optional[str] = None) -> List[dict[str, Any]]:
    """Lightweight index (title/description) — mirrors MCP runtime discovery."""
    token = _token()
    if not token:
        return []
    base = get_auth_base_url()
    params = {}
    if workspace_id:
        params["workspace_id"] = workspace_id
    try:
        resp = requests.get(
            f"{base}/v1/skills",
            headers={"Authorization": f"Bearer {token}"},
            params=params or None,
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        return list(resp.json().get("skills") or [])
    except requests.RequestException:
        return []


def fetch_skill(skill_id: str) -> Optional[dict[str, Any]]:
    token = _token()
    if not token:
        return None
    base = get_auth_base_url()
    try:
        resp = requests.get(
            f"{base}/v1/skills/{skill_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("skill")
    except requests.RequestException:
        return None


def sync_skill(skill: Skill, *, share_to_workspace: bool = False) -> Optional[str]:
    """
    Create or update a skill on the backend. Returns remote id on success.
    Local-first: failure is non-fatal for the caller.
    """
    token = _token()
    if not token:
        return None
    base = get_auth_base_url()
    payload = {
        "title": skill.title,
        "description": skill.description,
        "content": skill.content,
        "scope": "workspace" if share_to_workspace else skill.scope,
    }
    if skill.remote_id:
        try:
            resp = requests.patch(
                f"{base}/v1/skills/{skill.remote_id}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=20,
            )
            if resp.status_code == 200:
                return skill.remote_id
        except requests.RequestException:
            return None

    try:
        resp = requests.post(
            f"{base}/v1/skills",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=20,
        )
        if resp.status_code in (200, 201):
            data = resp.json().get("skill") or {}
            return str(data.get("id") or "") or None
    except requests.RequestException:
        return None
    return None


def delete_remote_skill(skill_id: str) -> bool:
    token = _token()
    if not token:
        return False
    base = get_auth_base_url()
    try:
        resp = requests.delete(
            f"{base}/v1/skills/{skill_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        return resp.status_code in (200, 204)
    except requests.RequestException:
        return False


def share_skill_to_workspace(skill_id: str) -> bool:
    token = _token()
    if not token:
        return False
    base = get_auth_base_url()
    try:
        resp = requests.post(
            f"{base}/v1/skills/{skill_id}/share",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False
