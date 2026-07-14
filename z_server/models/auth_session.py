"""Auth session, verification challenge, and OAuth state models."""

from __future__ import annotations

import enum
import secrets
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from z_server.models.base import Base
from z_server.models.user import AuthProvider


class ChallengePurpose(str, enum.Enum):
    email_otp = "email_otp"
    email_magic = "email_magic"
    phone_otp = "phone_otp"


class AuthSession(Base):
    """Long-lived CLI/web session token issued after successful sign-in."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    refresh_token_hash: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    provider: Mapped[AuthProvider] = mapped_column(
        Enum(AuthProvider, name="auth_provider", native_enum=False, create_constraint=False),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


class VerificationChallenge(Base):
    """Pending email/phone verification (OTP or magic-link session)."""

    __tablename__ = "verification_challenges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purpose: Mapped[ChallengePurpose] = mapped_column(
        Enum(ChallengePurpose, name="challenge_purpose", native_enum=False),
        nullable=False,
    )
    email: Mapped[str | None] = mapped_column(String(320), index=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    code_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Twilio Verify sid or opaque session id for magic links
    external_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set when magic-link confirms so CLI polling can pick up the issued session
    issued_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class OAuthState(Base):
    """PKCE state for Google browser OAuth (CLI loopback flow)."""

    __tablename__ = "oauth_states"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def new_opaque_token(prefix: str = "z_") -> str:
    return prefix + secrets.token_urlsafe(32)


# Avoid circular import at type-check time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from z_server.models.user import User
