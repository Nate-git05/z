"""Pydantic request/response schemas for auth endpoints."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class EmailStartRequest(BaseModel):
    email: EmailStr
    name: str | None = None
    method: str | None = Field(default="otp", description="otp or magic_link")


class EmailVerifyRequest(BaseModel):
    email: EmailStr
    code: str
    name: str | None = None


class PhoneStartRequest(BaseModel):
    phone: str = Field(..., description="E.164 phone number, e.g. +15551234567")


class PhoneVerifyRequest(BaseModel):
    phone: str
    code: str


class GoogleExchangeRequest(BaseModel):
    code: str
    code_verifier: str
    redirect_uri: str


class RefreshRequest(BaseModel):
    refresh_token: str


class CliBridgeCompleteRequest(BaseModel):
    """Browser posts session payload so the CLI can poll when localhost is blocked."""

    state: str = Field(..., min_length=8, max_length=128)
    data: dict


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int | None = None
    expires_at: float | None = None
    user: dict
    workspace: dict
