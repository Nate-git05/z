"""Uncertainty tree API — persist and share nodes across workspace sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from z_server.db import get_db
from z_server.models import User, WorkspaceMembership
from z_server.models.uncertainty import UncertaintyNodeRow, UncertaintyTask
from z_server.services.deps import get_current_user, get_primary_workspace

router = APIRouter(prefix="/v1/uncertainty", tags=["uncertainty"])

_RISK_ORDER = {"High": 0, "Medium": 1, "Low": 2}


class NodeUpsertRequest(BaseModel):
    repo_key: str
    workspace_id: str | None = None
    node: dict[str, Any]


class NodePatchRequest(BaseModel):
    status: str | None = None
    escalation_status: str | None = None
    escalated_to_user_id: str | None = None
    repo_key: str | None = None


class TaskCreateRequest(BaseModel):
    repo_key: str
    title: str
    checklist: dict[str, Any] = Field(default_factory=dict)
    workspace_id: str | None = None
    created_by_session: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _user_workspace_ids(db: Session, user: User) -> list[UUID]:
    rows = db.execute(
        select(WorkspaceMembership.workspace_id).where(WorkspaceMembership.user_id == user.id)
    ).all()
    return [r[0] for r in rows]


def _resolve_workspace_id(
    db: Session, user: User, workspace_id: str | None
) -> UUID | None:
    if workspace_id:
        try:
            wid = UUID(workspace_id)
        except ValueError:
            raise HTTPException(400, "Invalid workspace_id")
        ids = _user_workspace_ids(db, user)
        if wid not in ids:
            raise HTTPException(403, "Not a member of this workspace")
        return wid
    ws = get_primary_workspace(db, user)
    return ws.id if ws else None


def _parse_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


@router.post("/nodes")
def upsert_node(
    body: NodeUpsertRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create or update an uncertainty node (workspace-visible memory layer)."""
    wid = _resolve_workspace_id(db, user, body.workspace_id)
    raw = body.node or {}
    node_id = _parse_uuid(raw.get("id")) or uuid.uuid4()

    existing = db.get(UncertaintyNodeRow, node_id)
    task_uuid = _parse_uuid(raw.get("task_id"))

    fields = dict(
        workspace_id=wid,
        task_id=task_uuid,
        created_by_user_id=user.id,
        repo_key=body.repo_key,
        title=raw.get("title") or "Untitled",
        node_type=raw.get("type") or "Edge Case",
        confidence_tier=raw.get("confidence_tier") or "Medium",
        risk_tier=raw.get("risk_tier") or "Medium",
        summary=raw.get("summary") or "",
        explanation=raw.get("explanation") or "",
        files_affected=list(raw.get("files_affected") or []),
        symbols_affected=list(raw.get("symbols_affected") or []),
        why_uncertain=raw.get("why_uncertain") or "",
        what_could_go_wrong=raw.get("what_could_go_wrong") or "",
        suggested_fix=raw.get("suggested_fix") or "",
        suggested_tests=list(raw.get("suggested_tests") or []),
        suggested_prompt=raw.get("suggested_prompt") or "",
        status=raw.get("status") or "Open",
        area=raw.get("area") or "Other",
        signals=dict(raw.get("signals") or {}),
        created_by_session=raw.get("created_by_session"),
    )
    if raw.get("resolved_at"):
        try:
            fields["resolved_at"] = datetime.fromisoformat(raw["resolved_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            pass

    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        db.commit()
        db.refresh(existing)
        return {"node": existing.to_api_dict()}

    row = UncertaintyNodeRow(id=node_id, **fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"node": row.to_api_dict()}


@router.get("/nodes")
def list_nodes(
    repo_key: str = Query(...),
    workspace_id: str | None = None,
    include_resolved: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List uncertainty nodes for a repo, visible across the shared workspace."""
    wid = _resolve_workspace_id(db, user, workspace_id)
    q = select(UncertaintyNodeRow).where(UncertaintyNodeRow.repo_key == repo_key)
    if wid:
        q = q.where(UncertaintyNodeRow.workspace_id == wid)
    else:
        q = q.where(UncertaintyNodeRow.created_by_user_id == user.id)

    rows = db.execute(q).scalars().all()
    nodes = [r.to_api_dict() for r in rows]
    if not include_resolved:
        nodes = [n for n in nodes if n.get("status") not in ("Resolved", "Ignored")]
    nodes.sort(
        key=lambda n: (
            _RISK_ORDER.get(n.get("risk_tier") or "Low", 9),
            n.get("created_at") or "",
        )
    )
    return {"nodes": nodes}


@router.get("/nodes/{node_id}")
def get_node(
    node_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(UncertaintyNodeRow, node_id)
    if not row:
        raise HTTPException(404, "Node not found")
    # Membership check
    if row.workspace_id and row.workspace_id not in _user_workspace_ids(db, user):
        if row.created_by_user_id != user.id:
            raise HTTPException(403, "Forbidden")
    elif row.created_by_user_id != user.id and not row.workspace_id:
        raise HTTPException(403, "Forbidden")
    return {"node": row.to_api_dict()}


@router.patch("/nodes/{node_id}")
def patch_node(
    node_id: UUID,
    body: NodePatchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(UncertaintyNodeRow, node_id)
    if not row:
        raise HTTPException(404, "Node not found")
    if body.status:
        row.status = body.status
        if body.status in ("Resolved", "Ignored"):
            row.resolved_at = _utcnow()
    if body.escalation_status is not None:
        row.escalation_status = body.escalation_status
    if body.escalated_to_user_id:
        row.escalated_to_user_id = _parse_uuid(body.escalated_to_user_id)
    db.commit()
    db.refresh(row)
    return {"node": row.to_api_dict()}


@router.post("/tasks")
def create_task(
    body: TaskCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wid = _resolve_workspace_id(db, user, body.workspace_id)
    task = UncertaintyTask(
        workspace_id=wid,
        created_by_user_id=user.id,
        repo_key=body.repo_key,
        title=body.title,
        checklist=body.checklist or {},
        created_by_session=body.created_by_session,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return {
        "task": {
            "id": str(task.id),
            "title": task.title,
            "repo_key": task.repo_key,
            "checklist": task.checklist,
            "workspace_id": str(task.workspace_id) if task.workspace_id else None,
            "created_by_session": task.created_by_session,
            "created_at": task.created_at.isoformat() if task.created_at else None,
        }
    }
