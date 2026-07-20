"""Public waitlist API — no auth required; IP rate-limited."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from z_server.db import get_db
from z_server.models.waitlist import WaitlistSignup

router = APIRouter(prefix="/v1/waitlist", tags=["waitlist"])

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Simple in-memory IP rate limit: max N requests per window
_RATE_LIMIT = 10
_RATE_WINDOW_SEC = 60 * 15  # 15 minutes
_hits: dict[str, deque[float]] = defaultdict(deque)
_hits_lock = Lock()


class WaitlistRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=320)
    interest: str | None = Field(None, max_length=64)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > _RATE_WINDOW_SEC:
            q.popleft()
        if len(q) >= _RATE_LIMIT:
            raise HTTPException(429, "Too many waitlist submissions. Try again later.")
        q.append(now)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _validate_email(email: str) -> str:
    email = _normalize_email(email)
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email address.")
    return email


@router.post("")
@router.post("/")
def join_waitlist(
    body: WaitlistRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Public waitlist signup. Idempotent on email — duplicates return success.
    """
    _check_rate_limit(_client_ip(request))

    first = (body.first_name or "").strip()
    last = (body.last_name or "").strip()
    if not first or not last:
        raise HTTPException(400, "First and last name are required.")

    email = _validate_email(body.email)
    interest = (body.interest or "").strip() or None
    if interest and len(interest) > 64:
        raise HTTPException(400, "Interest tag is too long.")

    existing = db.execute(
        select(WaitlistSignup).where(WaitlistSignup.email == email)
    ).scalars().first()
    if existing:
        # Backfill interest on re-submit if the row has none yet.
        if interest and not existing.interest:
            existing.interest = interest
            try:
                db.commit()
            except Exception:
                db.rollback()
        return {
            "ok": True,
            "already_registered": True,
            "message": "You're on the list",
        }

    row = WaitlistSignup(
        first_name=first,
        last_name=last,
        email=email,
        interest=interest,
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        # Race on unique email — treat as success
        existing = db.execute(
            select(WaitlistSignup).where(WaitlistSignup.email == email)
        ).scalars().first()
        if existing:
            return {
                "ok": True,
                "already_registered": True,
                "message": "You're on the list",
            }
        raise

    return {
        "ok": True,
        "already_registered": False,
        "message": "You're on the list",
    }
