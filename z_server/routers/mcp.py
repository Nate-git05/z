"""MCP connection API — catalog, connect, disconnect, runtime for CLI."""

from __future__ import annotations

import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from uuid import UUID

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from z_server.config import get_settings
from z_server.db import get_db
from z_server.models import OAuthState, User, WorkspaceMembership
from z_server.models.mcp import McpConnection, McpConnectionType
from z_server.schemas.mcp import McpConnectRequest
from z_server.services.crypto import decrypt_credentials, encrypt_credentials
from z_server.services.deps import get_current_user, get_primary_workspace
from z_server.services.mcp_catalog import get_catalog_entry, list_catalog

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(conn: McpConnection) -> dict:
    return {
        "id": str(conn.id),
        "server_name": conn.server_name,
        "display_name": conn.display_name,
        "connection_type": conn.connection_type.value,
        "scope": conn.scope,
        "config": conn.config or {},
        "enabled": conn.enabled,
        "status": conn.status,
        "created_at": conn.created_at.isoformat() if conn.created_at else None,
        "updated_at": conn.updated_at.isoformat() if conn.updated_at else None,
    }


def _user_workspace_ids(db: Session, user: User) -> list[UUID]:
    rows = db.execute(
        select(WorkspaceMembership.workspace_id).where(WorkspaceMembership.user_id == user.id)
    ).all()
    return [r[0] for r in rows]


def _connections_query(db: Session, user: User):
    ws_ids = _user_workspace_ids(db, user)
    clauses = [McpConnection.user_id == user.id]
    if ws_ids:
        clauses.append(McpConnection.workspace_id.in_(ws_ids))
    return select(McpConnection).where(or_(*clauses)).order_by(McpConnection.created_at.desc())


@router.get("/catalog")
def mcp_catalog():
    """List MCP servers available to connect in the dashboard."""
    return {"tools": list_catalog()}


@router.get("/connections")
def list_connections(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List the user's personal + workspace MCP connections (no secrets)."""
    rows = db.execute(_connections_query(db, user)).scalars().all()
    return {"connections": [_serialize(c) for c in rows]}


@router.get("/runtime")
def runtime_connections(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    CLI runtime payload: enabled connections with decrypted credentials.
    Only returned to an authenticated session — used by `z` on startup.
    """
    rows = (
        db.execute(_connections_query(db, user).where(McpConnection.enabled.is_(True)))
        .scalars()
        .all()
    )
    tools = []
    for conn in rows:
        tools.append(
            {
                **_serialize(conn),
                "credentials": decrypt_credentials(conn.encrypted_credentials),
            }
        )
    return {"tools": tools}


@router.post("/connect")
def connect_manual(
    payload: McpConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Connect an MCP server with manual credentials / config."""
    entry = get_catalog_entry(payload.server_name)
    if not entry:
        raise HTTPException(404, f"Unknown MCP server '{payload.server_name}'.")
    if entry.connection_type == "oauth" and not payload.credentials:
        raise HTTPException(
            400,
            "This server uses OAuth. Start via GET /v1/mcp/oauth/start?server_name=…",
        )

    scope = (payload.scope or "personal").lower()
    user_id = None
    workspace_id = None
    if scope == "workspace":
        ws = get_primary_workspace(db, user)
        if not ws:
            raise HTTPException(400, "No workspace found for this account.")
        workspace_id = ws.id
    else:
        user_id = user.id

    # Merge catalog defaults with provided config; peel secrets into encrypted blob
    config = {**(entry.default_config or {}), **(payload.config or {})}
    secrets_payload: dict = {}
    for field in entry.fields:
        key = field["key"]
        if key not in payload.credentials and key not in config:
            if field.get("required"):
                raise HTTPException(400, f"Missing required field: {key}")
            continue
        value = payload.credentials.get(key, config.pop(key, None))
        if value is None or value == "":
            if field.get("required"):
                raise HTTPException(400, f"Missing required field: {key}")
            continue
        if field.get("secret"):
            secrets_payload[key] = value
        else:
            config[key] = value

    # Extra credential keys not in catalog fields
    for key, value in (payload.credentials or {}).items():
        if key not in secrets_payload and key not in config:
            secrets_payload[key] = value

    server_name = payload.server_name
    display_name = payload.display_name or entry.display_name
    if server_name == "custom" and payload.credentials.get("label"):
        display_name = str(payload.credentials["label"])
        # Allow multiple customs by uniquifying server_name
        slug = "".join(ch if ch.isalnum() else "-" for ch in display_name.lower())[:40]
        server_name = f"custom-{slug}-{secrets.token_hex(2)}"

    existing = _find_existing(db, user_id, workspace_id, server_name)
    if existing:
        existing.encrypted_credentials = (
            encrypt_credentials(secrets_payload) if secrets_payload else None
        )
        existing.config = config
        existing.display_name = display_name
        existing.enabled = True
        existing.status = "connected"
        existing.connection_type = McpConnectionType.manual
        db.flush()
        return {"connection": _serialize(existing), "updated": True}

    conn = McpConnection(
        user_id=user_id,
        workspace_id=workspace_id,
        server_name=server_name,
        display_name=display_name,
        connection_type=McpConnectionType.manual,
        encrypted_credentials=encrypt_credentials(secrets_payload) if secrets_payload else None,
        config=config,
        enabled=True,
        status="connected",
    )
    db.add(conn)
    db.flush()
    return {"connection": _serialize(conn), "updated": False}


def _find_existing(db, user_id, workspace_id, server_name):
    q = select(McpConnection).where(McpConnection.server_name == server_name)
    if user_id:
        q = q.where(McpConnection.user_id == user_id)
    else:
        q = q.where(McpConnection.workspace_id == workspace_id)
    return db.execute(q).scalars().first()


@router.post("/connections/{connection_id}/disconnect")
def disconnect(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = db.get(McpConnection, connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found.")
    if not _user_owns(db, user, conn):
        raise HTTPException(403, "Not allowed to disconnect this connection.")
    db.delete(conn)
    return {"ok": True, "disconnected": str(connection_id)}


@router.post("/connections/{connection_id}/toggle")
def toggle(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = db.get(McpConnection, connection_id)
    if not conn or not _user_owns(db, user, conn):
        raise HTTPException(404, "Connection not found.")
    conn.enabled = not conn.enabled
    conn.status = "connected" if conn.enabled else "disconnected"
    db.flush()
    return {"connection": _serialize(conn)}


def _user_owns(db: Session, user: User, conn: McpConnection) -> bool:
    if conn.user_id and conn.user_id == user.id:
        return True
    if conn.workspace_id and conn.workspace_id in _user_workspace_ids(db, user):
        return True
    return False


# ---------------------------------------------------------------------------
# OAuth connect flow for catalog entries that support it
# ---------------------------------------------------------------------------


@router.get("/oauth/start")
def oauth_start(
    server_name: str = Query(...),
    scope: str = Query("personal"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = get_catalog_entry(server_name)
    if not entry or entry.connection_type != "oauth":
        raise HTTPException(400, "Server does not support OAuth connect.")

    oauth = entry.oauth or {}
    client_id = os.environ.get(oauth.get("client_id_env", ""), "")
    if not client_id:
        raise HTTPException(
            500,
            f"OAuth not configured on server (missing {oauth.get('client_id_env')}).",
        )

    settings = get_settings()
    state = secrets.token_urlsafe(24)
    # Encode scope + server + user in state row
    redirect_uri = f"{settings.public_base_url}/v1/mcp/oauth/callback"
    db.add(
        OAuthState(
            state=state,
            code_challenge=f"mcp:{server_name}:{scope}:{user.id}",
            redirect_uri=redirect_uri,
            expires_at=_utcnow() + timedelta(minutes=15),
        )
    )
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": oauth.get("scopes", ""),
        "state": state,
        "response_type": "code",
    }
    url = oauth["authorize_url"] + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@router.get("/oauth/callback")
def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    row = db.execute(select(OAuthState).where(OAuthState.state == state)).scalars().first()
    if not row:
        raise HTTPException(400, "Invalid OAuth state.")
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < _utcnow():
        raise HTTPException(400, "OAuth state expired.")

    # code_challenge stores mcp:server:scope:user_id
    try:
        _, server_name, scope, user_id_str = row.code_challenge.split(":", 3)
    except ValueError as err:
        raise HTTPException(400, "Corrupt OAuth state.") from err

    entry = get_catalog_entry(server_name)
    if not entry:
        raise HTTPException(400, "Unknown server in OAuth state.")

    oauth = entry.oauth or {}
    client_id = os.environ.get(oauth.get("client_id_env", ""), "")
    client_secret = os.environ.get(oauth.get("client_secret_env", ""), "")
    token_url = oauth.get("token_url")
    if not token_url or not client_id:
        raise HTTPException(500, "OAuth token endpoint not configured.")

    resp = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": row.redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise HTTPException(400, f"OAuth token exchange failed: {resp.text}")
    tokens = resp.json()
    access_token = tokens.get("access_token")
    if not access_token:
        raise HTTPException(400, "OAuth response missing access_token.")

    user = db.get(User, UUID(user_id_str))
    if not user:
        raise HTTPException(400, "User not found for OAuth state.")

    user_id = None
    workspace_id = None
    if scope == "workspace":
        ws = get_primary_workspace(db, user)
        if not ws:
            raise HTTPException(400, "No workspace.")
        workspace_id = ws.id
    else:
        user_id = user.id

    secrets_payload = {
        "access_token": access_token,
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type", "bearer"),
    }
    existing = _find_existing(db, user_id, workspace_id, server_name)
    if existing:
        existing.encrypted_credentials = encrypt_credentials(secrets_payload)
        existing.config = {**(entry.default_config or {}), **(existing.config or {})}
        existing.enabled = True
        existing.status = "connected"
        existing.connection_type = McpConnectionType.oauth
    else:
        db.add(
            McpConnection(
                user_id=user_id,
                workspace_id=workspace_id,
                server_name=server_name,
                display_name=entry.display_name,
                connection_type=McpConnectionType.oauth,
                encrypted_credentials=encrypt_credentials(secrets_payload),
                config=dict(entry.default_config or {}),
                enabled=True,
                status="connected",
            )
        )
    db.delete(row)
    settings = get_settings()
    return RedirectResponse(f"{settings.public_base_url}/app/integrations?connected={server_name}")
