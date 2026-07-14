"""Remote sync of uncertainty nodes to z_server Postgres backend."""

from __future__ import annotations

import os
from typing import Any, List, Optional

import requests

from aider.z.auth import current_session, get_auth_base_url

from .schema import UncertaintyNode


def _token() -> Optional[str]:
    creds = current_session()
    return (creds.access_token if creds else None) or os.environ.get("Z_ACCESS_TOKEN")


def sync_node(node: UncertaintyNode, *, repo_key: str, workspace_id: Optional[str] = None) -> bool:
    token = _token()
    if not token:
        return False
    base = get_auth_base_url()
    payload = {
        "repo_key": repo_key,
        "workspace_id": workspace_id,
        "node": node.to_dict(),
    }
    try:
        resp = requests.post(
            f"{base}/v1/uncertainty/nodes",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=15,
        )
        return resp.status_code in (200, 201)
    except requests.RequestException:
        return False


def fetch_workspace_nodes(*, repo_key: str, workspace_id: Optional[str] = None) -> List[dict[str, Any]]:
    token = _token()
    if not token:
        return []
    base = get_auth_base_url()
    params = {"repo_key": repo_key}
    if workspace_id:
        params["workspace_id"] = workspace_id
    try:
        resp = requests.get(
            f"{base}/v1/uncertainty/nodes",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        return list(resp.json().get("nodes") or [])
    except requests.RequestException:
        return []


def update_remote_status(node_id: str, status: str, *, repo_key: str) -> bool:
    token = _token()
    if not token:
        return False
    base = get_auth_base_url()
    try:
        resp = requests.patch(
            f"{base}/v1/uncertainty/nodes/{node_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": status, "repo_key": repo_key},
            timeout=15,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False
