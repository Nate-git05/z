"""Shared FastAPI dependencies (current user / workspace)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from z_server.db import get_db
from z_server.models import AuthSession, User, Workspace, WorkspaceMembership
from z_server.services.tokens import hash_token


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("authorization") or ""
    # Also accept session cookie set by the web dashboard
    token = None
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
    else:
        token = request.cookies.get("z_session")

    if not token:
        raise HTTPException(401, "Not authenticated.")

    session = (
        db.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        .scalars()
        .first()
    )
    if not session or session.revoked_at or _as_utc(session.expires_at) < _utcnow():
        raise HTTPException(401, "Invalid or expired session.")
    if not session.user or not session.user.is_active:
        raise HTTPException(401, "User inactive.")
    return session.user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


def get_primary_workspace(db: Session, user: User) -> Workspace | None:
    membership = (
        db.execute(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.user_id == user.id)
            .order_by(WorkspaceMembership.created_at.asc())
        )
        .scalars()
        .first()
    )
    return membership.workspace if membership else None
