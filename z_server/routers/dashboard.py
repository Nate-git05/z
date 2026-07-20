"""Web dashboard pages (Jinja) — Integrations / MCP."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from z_server.db import get_db
from z_server.models import AuthProvider, OAuthState, User
from z_server.routers import mcp as mcp_api
from z_server.schemas.auth import EmailVerifyRequest
from z_server.schemas.mcp import McpConnectRequest
from z_server.services import google_oauth
from z_server.services.deps import get_current_user, get_optional_user, get_primary_workspace
from z_server.services.mcp_catalog import get_catalog_entry, list_catalog
from z_server.services.tokens import (
    find_or_create_user_by_email,
    find_or_create_user_by_google,
    issue_session,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["dashboard"])


def _ctx(request: Request, user: User | None = None, **extra):
    return {
        "request": request,
        "user": user,
        "accent": "#C96A2B",
        **extra,
    }


def _frontend_redirect(path: str) -> RedirectResponse | None:
    """Send browsers to the Next.js app when Z_FRONTEND_URL is configured."""
    from z_server.config import get_settings

    base = get_settings().frontend_url
    if not base:
        return None
    if not path.startswith("/"):
        path = f"/{path}"
    return RedirectResponse(f"{base}{path}", status_code=307)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, user: User | None = Depends(get_optional_user)):
    """Public landing page with waitlist — always at /."""
    redirect = _frontend_redirect("/")
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "landing.html", _ctx(request, user))


@router.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request, user: User | None = Depends(get_optional_user)):
    """Public pricing page — BYOK free forever, router coming soon."""
    redirect = _frontend_redirect("/pricing")
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "pricing.html", _ctx(request, user))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(get_optional_user)):
    redirect = _frontend_redirect("/login")
    if redirect:
        return redirect
    if user:
        return RedirectResponse("/app/integrations")
    return templates.TemplateResponse("login.html", _ctx(request, user, error=None))


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    name: str = Form(""),
    code: str = Form("000000"),
    db: Session = Depends(get_db),
):
    """Web login via email OTP; in Z_SERVER_DEV, code 000000 is accepted."""
    from z_server.config import get_settings
    from z_server.routers.auth import email_verify

    settings = get_settings()
    try:
        if settings.dev_mode and code.strip() == "000000":
            user = find_or_create_user_by_email(db, email.strip().lower(), name or None)
            tokens = issue_session(db, user, AuthProvider.email)
        else:
            tokens = email_verify(
                EmailVerifyRequest(email=email, code=code, name=name or None),
                request,
                db,
            )
    except HTTPException as err:
        return templates.TemplateResponse(
            "login.html",
            _ctx(request, None, error=err.detail),
            status_code=400,
        )

    response = RedirectResponse("/app/integrations", status_code=303)
    response.set_cookie(
        "z_session",
        tokens["access_token"],
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@router.post("/logout")
def web_logout():
    from z_server.config import get_settings

    dest = f"{get_settings().frontend_url}/" if get_settings().frontend_url else "/"
    response = RedirectResponse(dest, status_code=303)
    response.delete_cookie("z_session")
    return response


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _app_login_response(
    request: Request,
    *,
    redirect_uri: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session_payload: dict | None = None,
):
    params = {}
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    if state:
        params["state"] = state
    google_start = "/app/login/google/start"
    if params:
        google_start = f"{google_start}?{urlencode(params)}"
    return templates.TemplateResponse(
        request,
        "app_login.html",
        _ctx(
            request,
            None,
            redirect_uri=redirect_uri or "",
            callback_state=state or "",
            error=error,
            session_json=json.dumps(session_payload) if session_payload else "",
            signed_in=bool(session_payload),
            google_start_url=google_start,
        ),
    )


@router.get("/app/login", response_class=HTMLResponse)
def app_login_page(
    request: Request,
    redirect_uri: str | None = Query(None),
    state: str | None = Query(None),
):
    """CLI / browser sign-in page (Google + Continue with Z)."""
    return _app_login_response(request, redirect_uri=redirect_uri, state=state)


@router.get("/app/login/google/start")
def app_login_google_start(
    request: Request,
    db: Session = Depends(get_db),
    redirect_uri: str | None = Query(None),
    state: str | None = Query(None),
):
    """Begin Google OAuth for the web login page; returns to /app/login/google/callback."""
    from z_server.config import get_settings

    settings = get_settings()
    verifier, challenge = _pkce_pair()
    oauth_state = secrets.token_urlsafe(24)
    callback = f"{settings.public_base_url}/app/login/google/callback"
    db.add(
        OAuthState(
            state=oauth_state,
            code_challenge=challenge,
            redirect_uri=callback,
            expires_at=_utcnow() + timedelta(minutes=15),
        )
    )
    db.flush()
    try:
        url = google_oauth.build_google_authorize_url(
            redirect_uri=callback,
            state=oauth_state,
            code_challenge=challenge,
        )
    except google_oauth.GoogleOAuthError as err:
        return _app_login_response(
            request,
            redirect_uri=redirect_uri,
            state=state,
            error=str(err),
        )

    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        "z_oauth_verifier",
        verifier,
        httponly=True,
        samesite="lax",
        max_age=900,
    )
    # Preserve CLI loopback callback across the Google redirect.
    response.set_cookie(
        "z_cli_callback",
        json.dumps({"redirect_uri": redirect_uri or "", "state": state or ""}),
        httponly=True,
        samesite="lax",
        max_age=900,
    )
    return response


@router.get("/app/login/google/callback", response_class=HTMLResponse)
def app_login_google_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    from z_server.config import get_settings

    cli_raw = request.cookies.get("z_cli_callback") or "{}"
    try:
        cli = json.loads(cli_raw)
    except json.JSONDecodeError:
        cli = {}
    redirect_uri = cli.get("redirect_uri") or ""
    cli_state = cli.get("state") or ""

    if error:
        return _app_login_response(
            request,
            redirect_uri=redirect_uri,
            state=cli_state,
            error=f"Google sign-in was cancelled ({error}).",
        )
    if not code or not state:
        return _app_login_response(
            request,
            redirect_uri=redirect_uri,
            state=cli_state,
            error="Google sign-in did not return a code.",
        )

    oauth_state = (
        db.execute(select(OAuthState).where(OAuthState.state == state))
        .scalars()
        .first()
    )
    verifier = request.cookies.get("z_oauth_verifier")
    settings = get_settings()
    callback = f"{settings.public_base_url}/app/login/google/callback"
    if not oauth_state or _as_utc(oauth_state.expires_at) < _utcnow():
        return _app_login_response(
            request,
            redirect_uri=redirect_uri,
            state=cli_state,
            error="Google sign-in expired. Try again.",
        )
    if not verifier:
        return _app_login_response(
            request,
            redirect_uri=redirect_uri,
            state=cli_state,
            error="Missing OAuth verifier. Try again.",
        )

    try:
        profile = google_oauth.exchange_google_code(
            code=code,
            code_verifier=verifier,
            redirect_uri=callback,
        )
    except google_oauth.GoogleOAuthError as err:
        return _app_login_response(
            request,
            redirect_uri=redirect_uri,
            state=cli_state,
            error=str(err),
        )

    if not profile.get("sub"):
        return _app_login_response(
            request,
            redirect_uri=redirect_uri,
            state=cli_state,
            error="Google profile missing subject.",
        )

    user = find_or_create_user_by_google(
        db,
        google_sub=profile["sub"],
        email=profile.get("email"),
        name=profile.get("name"),
    )
    db.delete(oauth_state)
    tokens = issue_session(
        db,
        user,
        AuthProvider.google,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    response = _app_login_response(
        request,
        redirect_uri=redirect_uri,
        state=cli_state,
        session_payload=tokens,
    )
    response.set_cookie(
        "z_session",
        tokens["access_token"],
        httponly=True,
        samesite="lax",
        max_age=settings.access_token_ttl_seconds,
    )
    response.delete_cookie("z_oauth_verifier")
    response.delete_cookie("z_cli_callback")
    return response


@router.get("/app/integrations", response_class=HTMLResponse)
def integrations_page(
    request: Request,
    connected: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    catalog = list_catalog()
    rows = db.execute(mcp_api._connections_query(db, user)).scalars().all()
    connections = [mcp_api._serialize(c) for c in rows]
    connected_names = {
        c["server_name"]
        for c in connections
        if not c["server_name"].startswith("custom-")
    }
    # Also mark base "custom" if any custom-* exist
    if any(c["server_name"].startswith("custom") for c in connections):
        connected_names.add("custom")
    workspace = get_primary_workspace(db, user)
    return templates.TemplateResponse(
        "integrations.html",
        _ctx(
            request,
            user,
            workspace=workspace,
            catalog=catalog,
            connections=connections,
            connected_names=connected_names,
            flash=f"Connected {connected}" if connected else None,
        ),
    )


@router.get("/app/integrations/connect/{server_name}", response_class=HTMLResponse)
def connect_form(
    server_name: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = get_catalog_entry(server_name)
    if not entry:
        raise HTTPException(404, "Unknown MCP server")
    if entry.connection_type == "oauth":
        return RedirectResponse(
            f"/v1/mcp/oauth/start?server_name={server_name}&scope=personal"
        )
    workspace = get_primary_workspace(db, user)
    return templates.TemplateResponse(
        "connect_form.html",
        _ctx(request, user, workspace=workspace, tool=entry.to_dict(), error=None),
    )


@router.post("/app/integrations/connect/{server_name}", response_class=HTMLResponse)
async def connect_submit(
    server_name: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = get_catalog_entry(server_name)
    if not entry:
        raise HTTPException(404, "Unknown MCP server")

    form = await request.form()
    scope = str(form.get("scope") or "personal")
    credentials: dict = {}
    config: dict = {}
    display_name = None
    for field in entry.fields:
        key = field["key"]
        val = form.get(key)
        if val is None:
            continue
        val = str(val).strip()
        if not val:
            continue
        if key == "label":
            display_name = val
        if field.get("secret"):
            credentials[key] = val
        else:
            config[key] = val
            credentials[key] = val

    try:
        mcp_api.connect_manual(
            McpConnectRequest(
                server_name=server_name,
                scope=scope,
                credentials=credentials,
                config=config,
                display_name=display_name,
            ),
            user,
            db,
        )
    except HTTPException as err:
        workspace = get_primary_workspace(db, user)
        return templates.TemplateResponse(
            "connect_form.html",
            _ctx(
                request,
                user,
                workspace=workspace,
                tool=entry.to_dict(),
                error=str(err.detail),
            ),
            status_code=400,
        )
    return RedirectResponse("/app/integrations", status_code=303)


@router.post("/app/integrations/{connection_id}/disconnect")
def web_disconnect(
    connection_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mcp_api.disconnect(UUID(connection_id), user, db)
    return RedirectResponse("/app/integrations", status_code=303)


@router.post("/app/integrations/{connection_id}/toggle")
def web_toggle(
    connection_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mcp_api.toggle(UUID(connection_id), user, db)
    return RedirectResponse("/app/integrations", status_code=303)


# ----- Skills management (CLI-create / web-manage) -----


@router.get("/app/skills", response_class=HTMLResponse)
def skills_page(
    request: Request,
    flash: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from z_server.routers import skills as skills_api

    rows = db.execute(skills_api._skills_query(db, user)).scalars().all()
    personal = [s for s in rows if not s.workspace_id]
    shared = [s for s in rows if s.workspace_id]
    workspace = get_primary_workspace(db, user)
    return templates.TemplateResponse(
        "skills.html",
        _ctx(
            request,
            user,
            workspace=workspace,
            personal=personal,
            shared=shared,
            flash=flash,
        ),
    )


@router.get("/app/skills/{skill_id}", response_class=HTMLResponse)
def skill_detail(
    skill_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from z_server.routers import skills as skills_api

    try:
        skill = skills_api._get_owned(db, user, UUID(skill_id))
    except HTTPException:
        return RedirectResponse("/app/skills", status_code=303)
    workspace = get_primary_workspace(db, user)
    can_edit = skill.user_id == user.id
    return templates.TemplateResponse(
        "skill_detail.html",
        _ctx(
            request,
            user,
            workspace=workspace,
            skill=skill,
            can_edit=can_edit,
            error=None,
            saved=False,
        ),
    )


@router.post("/app/skills/{skill_id}", response_class=HTMLResponse)
async def skill_update(
    skill_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from z_server.routers import skills as skills_api
    from z_server.routers.skills import SkillUpdate

    try:
        skill = skills_api._get_owned(db, user, UUID(skill_id))
    except HTTPException:
        return RedirectResponse("/app/skills", status_code=303)

    form = await request.form()
    title = str(form.get("title") or "").strip()
    description = str(form.get("description") or "").strip()
    content = str(form.get("content") or "").strip()
    workspace = get_primary_workspace(db, user)

    try:
        skills_api.update_skill(
            UUID(skill_id),
            SkillUpdate(title=title, description=description, content=content),
            user,
            db,
        )
        skill = skills_api._get_owned(db, user, UUID(skill_id))
    except HTTPException as err:
        return templates.TemplateResponse(
            "skill_detail.html",
            _ctx(
                request,
                user,
                workspace=workspace,
                skill=skill,
                can_edit=skill.user_id == user.id,
                error=str(err.detail),
                saved=False,
            ),
            status_code=400,
        )

    return templates.TemplateResponse(
        "skill_detail.html",
        _ctx(
            request,
            user,
            workspace=workspace,
            skill=skill,
            can_edit=skill.user_id == user.id,
            error=None,
            saved=True,
        ),
    )


@router.post("/app/skills/{skill_id}/share")
def skill_share(
    skill_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from z_server.routers import skills as skills_api

    try:
        skills_api.share_skill(UUID(skill_id), user, db)
    except HTTPException:
        pass
    return RedirectResponse("/app/skills?flash=shared", status_code=303)


@router.post("/app/skills/{skill_id}/unshare")
def skill_unshare(
    skill_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from z_server.routers import skills as skills_api
    from z_server.routers.skills import SkillUpdate

    try:
        skills_api.update_skill(
            UUID(skill_id), SkillUpdate(scope="personal"), user, db
        )
    except HTTPException:
        pass
    return RedirectResponse("/app/skills?flash=unshared", status_code=303)


@router.post("/app/skills/{skill_id}/delete")
def skill_delete(
    skill_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from z_server.routers import skills as skills_api

    try:
        skills_api.delete_skill(UUID(skill_id), user, db)
    except HTTPException:
        pass
    return RedirectResponse("/app/skills?flash=deleted", status_code=303)
