"""Routing gateway HTTP API — models behind Z auth; logs every request."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from z_server.db import get_db
from z_server.models import User
from z_server.models.gateway import GatewayRequest
from z_server.schemas.gateway import (
    ChatCompletionRequest,
    RoutingOutcomeRequest,
    RoutingOutcomeResponse,
    UsageRow,
    UsageSummary,
)
from z_server.services.deps import get_current_user
from z_server.services.gateway_proxy import GatewayUpstreamError, proxy_chat_completion
from z_server.services.gateway_routing import ROUTING_POLICY_VERSION, record_gateway_outcome

router = APIRouter(prefix="/v1/gateway", tags=["gateway"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _log_request(
    db: Session,
    *,
    user: User,
    meta: dict,
    thread_id: Optional[str],
    task_mode: Optional[str],
) -> GatewayRequest:
    row = GatewayRequest(
        user_id=user.id,
        model_id=meta.get("model_id") or "unknown",
        tier=meta.get("tier"),
        input_tokens=meta.get("input_tokens"),
        output_tokens=meta.get("output_tokens"),
        cost_usd=meta.get("cost_usd"),
        latency_ms=meta.get("latency_ms"),
        status=meta.get("status") or "ok",
        thread_id=thread_id,
        task_mode=task_mode or meta.get("task_mode"),
        routing_policy_version=meta.get("routing_policy_version"),
        error_message=meta.get("error_message"),
    )
    db.add(row)
    db.flush()
    return row


@router.get("/health")
def gateway_health():
    return {
        "ok": True,
        "service": "z-gateway",
        "policy": ROUTING_POLICY_VERSION,
        "features": ["task_mode", "intent", "escalate", "calibration"],
    }


@router.post("/chat/completions")
def chat_completions(
    payload: ChatCompletionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    messages = [m.model_dump() for m in payload.messages]
    thread_id = payload.thread_id
    task_mode = payload.task_mode
    preferred = payload.preferred_model or payload.model
    try:
        body, meta = proxy_chat_completion(
            model=preferred,
            messages=messages,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            stream=payload.stream,
            task_mode=task_mode,
            intent=payload.intent,
            tier=payload.tier,
            escalate=bool(payload.escalate),
            escalation_depth=int(payload.escalation_depth or 0),
            customer_id=str(user.id),
        )
        _log_request(
            db,
            user=user,
            meta=meta,
            thread_id=thread_id,
            task_mode=task_mode,
        )
        return body
    except GatewayUpstreamError as err:
        _log_request(
            db,
            user=user,
            meta=err.meta,
            thread_id=thread_id,
            task_mode=task_mode,
        )
        raise HTTPException(err.status_code, err.message) from err


@router.post("/routing/outcome", response_model=RoutingOutcomeResponse)
def routing_outcome(
    payload: RoutingOutcomeRequest,
    user: User = Depends(get_current_user),
):
    """Calibration hook — local verify/commit gate reports pass/fail anonymously."""
    try:
        result = record_gateway_outcome(
            model_id=payload.model_id,
            tier=payload.tier,
            gate_passed=payload.gate_passed,
            escalated=payload.escalated,
            cost_usd=payload.cost_usd,
            customer_id=str(user.id),
            checker_triggered=payload.checker_triggered,
        )
        return RoutingOutcomeResponse(**result)
    except Exception as err:
        raise HTTPException(500, f"routing outcome failed: {err}") from err


@router.get("/usage", response_model=UsageSummary)
def usage_summary(
    range: str = Query("billing_period", pattern="^(billing_period|all)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Live aggregate from gateway_requests (D10 — no rollup table)."""
    q = select(
        GatewayRequest.model_id,
        func.count(GatewayRequest.id),
        func.coalesce(func.sum(GatewayRequest.input_tokens), 0),
        func.coalesce(func.sum(GatewayRequest.output_tokens), 0),
        func.coalesce(func.sum(GatewayRequest.cost_usd), 0.0),
    ).where(GatewayRequest.user_id == user.id)

    if range == "billing_period":
        # V0: calendar month UTC as stand-in for billing period
        now = _utcnow()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        q = q.where(GatewayRequest.created_at >= start)

    q = q.group_by(GatewayRequest.model_id)
    rows = db.execute(q).all()
    by_model = [
        UsageRow(
            model_id=r[0],
            requests=int(r[1]),
            input_tokens=int(r[2] or 0),
            output_tokens=int(r[3] or 0),
            cost_usd=float(r[4] or 0.0),
        )
        for r in rows
    ]
    total_requests = sum(r.requests for r in by_model)
    total_cost = sum(r.cost_usd for r in by_model)
    return UsageSummary(
        range=range,
        by_model=by_model,
        total_requests=total_requests,
        total_cost_usd=total_cost,
    )
