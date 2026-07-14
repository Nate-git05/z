"""Skills API — list index, get full content, create/update/delete, share to workspace."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from z_server.db import get_db
from z_server.models import User, WorkspaceMembership
from z_server.models.skill import Skill
from z_server.services.deps import get_current_user, get_primary_workspace

router = APIRouter(prefix="/v1/skills", tags=["skills"])


class SkillCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=500)
    content: str = Field(..., min_length=1)
    scope: str = "personal"  # personal | workspace


class SkillUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=500)
    content: str | None = None
    scope: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _user_workspace_ids(db: Session, user: User) -> list[UUID]:
    rows = db.execute(
        select(WorkspaceMembership.workspace_id).where(WorkspaceMembership.user_id == user.id)
    ).all()
    return [r[0] for r in rows]


def _skills_query(db: Session, user: User):
    ws_ids = _user_workspace_ids(db, user)
    clauses = [Skill.user_id == user.id]
    if ws_ids:
        clauses.append(Skill.workspace_id.in_(ws_ids))
    return select(Skill).where(or_(*clauses)).order_by(Skill.updated_at.desc())


def _get_owned(db: Session, user: User, skill_id: UUID) -> Skill:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(404, "Skill not found")
    ws_ids = _user_workspace_ids(db, user)
    if skill.user_id == user.id:
        return skill
    if skill.workspace_id and skill.workspace_id in ws_ids:
        return skill
    raise HTTPException(403, "Forbidden")


@router.get("")
@router.get("/")
def list_skills(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lightweight index — title/description only (no full content)."""
    rows = db.execute(_skills_query(db, user)).scalars().all()
    return {"skills": [s.to_index_dict() for s in rows]}


@router.get("/{skill_id}")
def get_skill(
    skill_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    skill = _get_owned(db, user, skill_id)
    return {"skill": skill.to_api_dict()}


@router.post("")
@router.post("/")
def create_skill(
    body: SkillCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    title = body.title.strip()
    description = (body.description or "").strip()
    content = body.content.strip()
    if not title or not content:
        raise HTTPException(400, "Title and content are required.")

    scope = (body.scope or "personal").lower()
    workspace_id = None
    user_id = user.id
    if scope == "workspace":
        ws = get_primary_workspace(db, user)
        if not ws:
            raise HTTPException(400, "No workspace to share into.")
        workspace_id = ws.id

    skill = Skill(
        user_id=user_id,
        workspace_id=workspace_id,
        title=title,
        description=description,
        content=content,
        created_by=user.display_name(),
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return {"skill": skill.to_api_dict()}


@router.patch("/{skill_id}")
def update_skill(
    skill_id: UUID,
    body: SkillUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    skill = _get_owned(db, user, skill_id)
    # Only creator (or personal owner) can edit workspace skills content
    if skill.workspace_id and skill.user_id and skill.user_id != user.id:
        raise HTTPException(403, "Only the creator can edit this shared skill.")

    if body.title is not None:
        skill.title = body.title.strip()
    if body.description is not None:
        skill.description = body.description.strip()
    if body.content is not None:
        skill.content = body.content.strip()
    if body.scope == "workspace" and not skill.workspace_id:
        ws = get_primary_workspace(db, user)
        if not ws:
            raise HTTPException(400, "No workspace to share into.")
        skill.workspace_id = ws.id
    elif body.scope == "personal" and skill.workspace_id:
        # Unshare — keep as personal
        skill.workspace_id = None
        skill.user_id = user.id

    skill.updated_at = _utcnow()
    db.commit()
    db.refresh(skill)
    return {"skill": skill.to_api_dict()}


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    skill = _get_owned(db, user, skill_id)
    if skill.workspace_id and skill.user_id and skill.user_id != user.id:
        raise HTTPException(403, "Only the creator can delete this shared skill.")
    db.delete(skill)
    db.commit()
    return {"ok": True}


@router.post("/{skill_id}/share")
def share_skill(
    skill_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a personal skill as shared to the user's primary workspace."""
    skill = _get_owned(db, user, skill_id)
    if skill.user_id != user.id:
        raise HTTPException(403, "Only the owner can share this skill.")
    ws = get_primary_workspace(db, user)
    if not ws:
        raise HTTPException(400, "No workspace to share into.")
    skill.workspace_id = ws.id
    skill.updated_at = _utcnow()
    db.commit()
    db.refresh(skill)
    return {"skill": skill.to_api_dict()}
