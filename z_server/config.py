"""Configuration for the Z auth web application."""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache
def get_settings() -> "Settings":
    return Settings()


class Settings:
    """Runtime settings loaded from environment variables."""

    def __init__(self) -> None:
        self.dev_mode: bool = os.environ.get("Z_SERVER_DEV", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        # Default: SQLite file for local / early testing (no Postgres required).
        # Production: set DATABASE_URL=postgresql+psycopg://user:pass@host:5432/z
        default_db = (
            "sqlite+pysqlite:///./z_server.db"
            if self.dev_mode
            else "postgresql+psycopg://z:z@localhost:5432/z"
        )
        self.database_url: str = os.environ.get("DATABASE_URL", default_db)
        self.secret_key: str = os.environ.get("Z_SECRET_KEY", "dev-change-me")
        self.access_token_ttl_seconds: int = int(
            os.environ.get("Z_ACCESS_TOKEN_TTL", str(60 * 60 * 24 * 30))
        )
        self.app_name: str = os.environ.get("Z_APP_NAME", "Z")
        self.public_base_url: str = os.environ.get(
            "Z_PUBLIC_BASE_URL", "http://127.0.0.1:8080"
        ).rstrip("/")
        # When set (e.g. https://zim-s.com), public marketing/auth pages
        # (/ , /pricing, /login) redirect to the Next.js frontend instead of
        # serving Jinja templates. Leave empty for API-only / local Jinja.
        self.frontend_url: str = os.environ.get("Z_FRONTEND_URL", "").rstrip("/")

        # Email (optional — falls back to logging the OTP in dev)
        self.smtp_host: str | None = os.environ.get("Z_SMTP_HOST")
        self.smtp_port: int = int(os.environ.get("Z_SMTP_PORT", "587"))
        self.smtp_user: str | None = os.environ.get("Z_SMTP_USER")
        self.smtp_password: str | None = os.environ.get("Z_SMTP_PASSWORD")
        self.email_from: str = os.environ.get("Z_EMAIL_FROM", "noreply@z.dev")

        # Twilio Verify
        self.twilio_account_sid: str | None = os.environ.get("TWILIO_ACCOUNT_SID")
        self.twilio_auth_token: str | None = os.environ.get("TWILIO_AUTH_TOKEN")
        self.twilio_verify_service_sid: str | None = os.environ.get(
            "TWILIO_VERIFY_SERVICE_SID"
        )

        # Google OAuth
        self.google_client_id: str | None = os.environ.get("Z_GOOGLE_CLIENT_ID") or os.environ.get(
            "GOOGLE_CLIENT_ID"
        )
        self.google_client_secret: str | None = os.environ.get(
            "Z_GOOGLE_CLIENT_SECRET"
        ) or os.environ.get("GOOGLE_CLIENT_SECRET")

        self.mcp_github_client_id: str | None = os.environ.get("Z_MCP_GITHUB_CLIENT_ID")
        self.mcp_github_client_secret: str | None = os.environ.get(
            "Z_MCP_GITHUB_CLIENT_SECRET"
        )
