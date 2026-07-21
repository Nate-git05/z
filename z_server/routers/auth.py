"""Auth HTTP routes — matches the Z CLI contract in aider/z/auth.py."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from z_server.config import get_settings
from z_server.db import get_db
from z_server.models import (
    AuthProvider,
    ChallengePurpose,
    OAuthState,
    VerificationChallenge,
)
from z_server.schemas.auth import (
    EmailStartRequest,
    EmailVerifyRequest,
    GoogleExchangeRequest,
    PhoneStartRequest,
    PhoneVerifyRequest,
    RefreshRequest,
)
from z_server.services import email as email_service
from z_server.services import google_oauth
from z_server.services import phone as phone_service
from z_server.services.tokens import (
    find_or_create_user_by_email,
    find_or_create_user_by_google,
    find_or_create_user_by_phone,
    hash_token,
    issue_session,
    refresh_session,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """Normalize DB datetimes (SQLite may return naive) for comparison."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


@router.post("/email/start")
def email_start(payload: EmailStartRequest, db: Session = Depends(get_db)):
    method = (payload.method or "otp").lower()
    if method not in ("otp", "magic_link"):
        raise HTTPException(400, "method must be otp or magic_link")

    email = str(payload.email).strip().lower()
    expires = _utcnow() + timedelta(minutes=10)

    if method == "magic_link":
        challenge = VerificationChallenge(
            purpose=ChallengePurpose.email_magic,
            email=email,
            name=payload.name,
            expires_at=expires,
            status="pending",
        )
        db.add(challenge)
        db.flush()
        settings = get_settings()
        link = f"{settings.public_base_url}/v1/auth/email/magic/{challenge.id}"
        email_service.send_magic_link(email, link, payload.name)
        return {"method": "magic_link", "session_id": str(challenge.id)}

    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = VerificationChallenge(
        purpose=ChallengePurpose.email_otp,
        email=email,
        name=payload.name,
        code_hash=hash_token(code),
        expires_at=expires,
        status="pending",
    )
    db.add(challenge)
    db.flush()
    email_service.send_email_otp(email, code, payload.name)
    return {"method": "otp", "session_id": str(challenge.id)}


@router.post("/email/verify")
def email_verify(payload: EmailVerifyRequest, request: Request, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()
    challenge = (
        db.execute(
            select(VerificationChallenge)
            .where(
                VerificationChallenge.email == email,
                VerificationChallenge.purpose == ChallengePurpose.email_otp,
                VerificationChallenge.status == "pending",
            )
            .order_by(VerificationChallenge.created_at.desc())
        )
        .scalars()
        .first()
    )
    if not challenge or _as_utc(challenge.expires_at) < _utcnow():
        raise HTTPException(400, "No valid email code challenge found.")
    if challenge.code_hash != hash_token(payload.code.strip()):
        # Dev convenience: accept 123456 when server is in dev mode
        settings = get_settings()
        if not (settings.dev_mode and payload.code.strip() == "123456"):
            raise HTTPException(400, "Invalid code.")

    challenge.status = "confirmed"
    challenge.confirmed_at = _utcnow()
    user = find_or_create_user_by_email(db, email, payload.name or challenge.name)
    tokens = issue_session(
        db,
        user,
        AuthProvider.email,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    # Also set the web session cookie so Next.js login works with /app/* pages.
    response = JSONResponse(content=tokens)
    response.set_cookie(
        "z_session",
        tokens["access_token"],
        httponly=True,
        samesite="lax",
        max_age=get_settings().access_token_ttl_seconds,
    )
    return response


@router.get("/email/magic/{challenge_id}", response_class=HTMLResponse)
def email_magic_confirm(challenge_id: str, db: Session = Depends(get_db)):
    challenge = db.get(VerificationChallenge, challenge_id)
    if not challenge or challenge.purpose != ChallengePurpose.email_magic:
        raise HTTPException(404, "Magic link not found.")
    if challenge.status == "confirmed" and challenge.issued_access_token:
        return HTMLResponse("<h2>Already signed in. Return to the Z terminal.</h2>")
    if _as_utc(challenge.expires_at) < _utcnow():
        raise HTTPException(400, "Magic link expired.")

    user = find_or_create_user_by_email(db, challenge.email, challenge.name)
    tokens = issue_session(db, user, AuthProvider.email)
    challenge.status = "confirmed"
    challenge.confirmed_at = _utcnow()
    challenge.issued_access_token = tokens["access_token"]
    challenge.issued_refresh_token = tokens.get("refresh_token")
    challenge.issued_user_id = user.id
    return HTMLResponse(
        "<html><body><h2>Signed in to Z.</h2>"
        "<p>You can close this tab and return to the terminal.</p></body></html>"
    )


@router.get("/email/session/{session_id}")
def email_session_status(session_id: str, db: Session = Depends(get_db)):
    challenge = db.get(VerificationChallenge, session_id)
    if not challenge:
        raise HTTPException(404, "Session not found.")
    if challenge.status != "confirmed" or not challenge.issued_access_token:
        return {"status": challenge.status}

    user = find_or_create_user_by_email(db, challenge.email, challenge.name)
    # Re-issue a clean payload shape for the CLI poller
    membership = user.memberships[0] if user.memberships else None
    workspace = membership.workspace if membership else None
    return {
        "status": "confirmed",
        "access_token": challenge.issued_access_token,
        "refresh_token": challenge.issued_refresh_token,
        "token_type": "Bearer",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "phone": user.phone,
            "provider": "email",
        },
        "workspace": {
            "id": str(workspace.id) if workspace else None,
            "name": workspace.name if workspace else None,
            "role": membership.role.value if membership else None,
        },
    }


# ---------------------------------------------------------------------------
# Phone (Twilio Verify)
# ---------------------------------------------------------------------------


@router.post("/phone/start")
def phone_start(payload: PhoneStartRequest, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    if not phone.startswith("+"):
        raise HTTPException(400, "Phone must be E.164, e.g. +15551234567")
    try:
        external_id = phone_service.start_phone_verification(phone)
    except phone_service.PhoneVerifyError as err:
        raise HTTPException(502, str(err)) from err

    challenge = VerificationChallenge(
        purpose=ChallengePurpose.phone_otp,
        phone=phone,
        external_id=external_id,
        expires_at=_utcnow() + timedelta(minutes=10),
        status="pending",
    )
    db.add(challenge)
    return {"ok": True, "session_id": str(challenge.id)}


@router.post("/phone/verify")
def phone_verify(payload: PhoneVerifyRequest, request: Request, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    try:
        ok = phone_service.check_phone_verification(phone, payload.code.strip())
    except phone_service.PhoneVerifyError as err:
        raise HTTPException(502, str(err)) from err
    if not ok:
        raise HTTPException(400, "Invalid verification code.")

    challenge = (
        db.execute(
            select(VerificationChallenge)
            .where(
                VerificationChallenge.phone == phone,
                VerificationChallenge.purpose == ChallengePurpose.phone_otp,
                VerificationChallenge.status == "pending",
            )
            .order_by(VerificationChallenge.created_at.desc())
        )
        .scalars()
        .first()
    )
    if challenge:
        challenge.status = "confirmed"
        challenge.confirmed_at = _utcnow()

    user = find_or_create_user_by_phone(db, phone)
    return issue_session(
        db,
        user,
        AuthProvider.phone,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )


# ---------------------------------------------------------------------------
# Google OAuth (CLI loopback)
# ---------------------------------------------------------------------------


@router.get("/google/start")
def google_start(
    redirect_uri: str = Query(...),
    state: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
    db: Session = Depends(get_db),
):
    if code_challenge_method != "S256":
        raise HTTPException(400, "Only S256 PKCE is supported.")
    db.add(
        OAuthState(
            state=state,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            expires_at=_utcnow() + timedelta(minutes=15),
        )
    )
    try:
        url = google_oauth.build_google_authorize_url(
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
        )
    except google_oauth.GoogleOAuthError as err:
        raise HTTPException(500, str(err)) from err
    return RedirectResponse(url)


@router.post("/google/exchange")
def google_exchange(
    payload: GoogleExchangeRequest, request: Request, db: Session = Depends(get_db)
):
    oauth_state = (
        db.execute(
            select(OAuthState)
            .where(OAuthState.redirect_uri == payload.redirect_uri)
            .order_by(OAuthState.created_at.desc())
        )
        .scalars()
        .first()
    )
    # State was validated by the CLI loopback; we still require a recent PKCE row
    if not oauth_state or _as_utc(oauth_state.expires_at) < _utcnow():
        raise HTTPException(400, "OAuth state expired or missing. Restart Google sign-in.")

    try:
        profile = google_oauth.exchange_google_code(
            code=payload.code,
            code_verifier=payload.code_verifier,
            redirect_uri=payload.redirect_uri,
        )
    except google_oauth.GoogleOAuthError as err:
        raise HTTPException(400, str(err)) from err

    if not profile.get("sub"):
        raise HTTPException(400, "Google profile missing subject.")

    user = find_or_create_user_by_google(
        db,
        google_sub=profile["sub"],
        email=profile.get("email"),
        name=profile.get("name"),
    )
    db.delete(oauth_state)
    return issue_session(
        db,
        user,
        AuthProvider.google,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )


@router.post("/refresh")
def refresh_tokens(
    payload: RefreshRequest, request: Request, db: Session = Depends(get_db)
):
    """Exchange a refresh token for a new access + refresh token pair."""
    tokens = refresh_session(
        db,
        payload.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    if not tokens:
        raise HTTPException(401, "Invalid or revoked refresh token.")
    response = JSONResponse(content=tokens)
    response.set_cookie(
        "z_session",
        tokens["access_token"],
        httponly=True,
        samesite="lax",
        max_age=get_settings().access_token_ttl_seconds,
    )
    return response


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token.")
    token = auth.split(" ", 1)[1].strip()
    from z_server.models import AuthSession

    session = (
        db.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        .scalars()
        .first()
    )
    if not session or session.revoked_at or _as_utc(session.expires_at) < _utcnow():
        raise HTTPException(401, "Invalid or expired token.")
    user = session.user
    membership = user.memberships[0] if user.memberships else None
    workspace = membership.workspace if membership else None
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "phone": user.phone,
            "provider": session.provider.value,
        },
        "workspace": {
            "id": str(workspace.id) if workspace else None,
            "name": workspace.name if workspace else None,
            "role": membership.role.value if membership else None,
        },
    }
