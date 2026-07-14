"""Email OTP / magic-link delivery."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from z_server.config import get_settings

logger = logging.getLogger("z_server.email")


def send_email_otp(to_email: str, code: str, name: str | None = None) -> None:
    settings = get_settings()
    subject = f"{settings.app_name} sign-in code"
    body = (
        f"Hi{(' ' + name) if name else ''},\n\n"
        f"Your {settings.app_name} sign-in code is: {code}\n\n"
        f"It expires in 10 minutes.\n"
    )
    _send(to_email, subject, body, fallback_log=f"DEV email OTP for {to_email}: {code}")


def send_magic_link(to_email: str, link: str, name: str | None = None) -> None:
    settings = get_settings()
    subject = f"Sign in to {settings.app_name}"
    body = (
        f"Hi{(' ' + name) if name else ''},\n\n"
        f"Open this link to finish signing in to {settings.app_name}:\n{link}\n\n"
        f"If you did not request this, ignore this email.\n"
    )
    _send(to_email, subject, body, fallback_log=f"DEV magic link for {to_email}: {link}")


def _send(to_email: str, subject: str, body: str, *, fallback_log: str) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        logger.warning(fallback_log)
        print(fallback_log, flush=True)
        return

    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
