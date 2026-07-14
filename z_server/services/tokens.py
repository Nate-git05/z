"""Token hashing and session issuance helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from z_server.config import get_settings
from z_server.models import (
    AuthProvider,
    AuthSession,
    MembershipRole,
    User,
    Workspace,
    WorkspaceMembership,
    new_opaque_token,
)


def hash_token(token: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def issue_session(
    db: Session,
    user: User,
    provider: AuthProvider,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> dict:
    """Create an AuthSession row and return the CLI-facing token payload."""
    settings = get_settings()
    access = new_opaque_token("z_")
    refresh = new_opaque_token("zref_")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.access_token_ttl_seconds)

    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(access),
        refresh_token_hash=hash_token(refresh),
        provider=provider,
        expires_at=expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    db.add(session)
    db.flush()

    membership = db.execute(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.user_id == user.id)
        .order_by(WorkspaceMembership.created_at.asc())
    ).scalars().first()
    workspace = membership.workspace if membership else None

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl_seconds,
        "expires_at": expires_at.timestamp(),
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "phone": user.phone,
            "provider": provider.value,
        },
        "workspace": {
            "id": str(workspace.id) if workspace else None,
            "name": workspace.name if workspace else None,
            "role": membership.role.value if membership else None,
        },
    }


def get_or_create_personal_workspace(db: Session, user: User) -> Workspace:
    existing = db.execute(
        select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
    ).scalars().first()
    if existing:
        return existing.workspace

    base_slug = (user.email or user.phone or str(user.id)).split("@")[0]
    slug = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in base_slug.lower())[:40]
    slug = f"{slug}-{secrets.token_hex(3)}"
    workspace = Workspace(name="Personal", slug=slug)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMembership(
            user_id=user.id,
            workspace_id=workspace.id,
            role=MembershipRole.owner,
        )
    )
    db.flush()
    return workspace


def find_or_create_user_by_email(db: Session, email: str, name: str | None) -> User:
    email = email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalars().first()
    if user:
        if name and not user.name:
            user.name = name
        return user
    user = User(email=email, name=name, primary_provider=AuthProvider.email)
    db.add(user)
    db.flush()
    get_or_create_personal_workspace(db, user)
    return user


def find_or_create_user_by_phone(db: Session, phone: str, name: str | None = None) -> User:
    user = db.execute(select(User).where(User.phone == phone)).scalars().first()
    if user:
        return user
    user = User(phone=phone, name=name, primary_provider=AuthProvider.phone)
    db.add(user)
    db.flush()
    get_or_create_personal_workspace(db, user)
    return user


def find_or_create_user_by_google(
    db: Session,
    *,
    google_sub: str,
    email: str | None,
    name: str | None,
) -> User:
    user = db.execute(select(User).where(User.google_sub == google_sub)).scalars().first()
    if not user and email:
        user = db.execute(select(User).where(User.email == email.lower())).scalars().first()
        if user:
            user.google_sub = google_sub
    if user:
        if name and not user.name:
            user.name = name
        if email and not user.email:
            user.email = email.lower()
        return user
    user = User(
        google_sub=google_sub,
        email=email.lower() if email else None,
        name=name,
        primary_provider=AuthProvider.google,
    )
    db.add(user)
    db.flush()
    get_or_create_personal_workspace(db, user)
    return user
