"""Web dashboard pages (Jinja) — Integrations / MCP."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from z_server.db import get_db
from z_server.models import AuthProvider, User
from z_server.routers import mcp as mcp_api
from z_server.schemas.auth import EmailVerifyRequest
from z_server.schemas.mcp import McpConnectRequest
from z_server.services.deps import get_current_user, get_optional_user, get_primary_workspace
from z_server.services.mcp_catalog import get_catalog_entry, list_catalog
from z_server.services.tokens import find_or_create_user_by_email, issue_session

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


@router.get("/", response_class=HTMLResponse)
def home(request: Request, user: User | None = Depends(get_optional_user)):
    """Public landing page with waitlist — always at /."""
    return templates.TemplateResponse(request, "landing.html", _ctx(request, user))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(get_optional_user)):
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
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("z_session")
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
