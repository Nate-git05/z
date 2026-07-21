"""Twilio Verify phone authentication."""

from __future__ import annotations

import logging

from z_server.config import get_settings

logger = logging.getLogger("z_server.phone")


class PhoneVerifyError(Exception):
    pass


def start_phone_verification(phone: str) -> str | None:
    """
    Start Twilio Verify SMS. Returns an external id when available.
    In dev (no Twilio creds), logs the phone and returns None.
    """
    settings = get_settings()
    if not (
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_verify_service_sid
    ):
        logger.warning("DEV phone verify start for %s (Twilio not configured)", phone)
        print(f"DEV phone verify start for {phone} — use code 123456", flush=True)
        return None

    try:
        from twilio.rest import Client

        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        verification = client.verify.v2.services(
            settings.twilio_verify_service_sid
        ).verifications.create(to=phone, channel="sms")
        return verification.sid
    except Exception as err:
        raise PhoneVerifyError(f"Twilio Verify start failed: {err}") from err


def check_phone_verification(phone: str, code: str) -> bool:
    settings = get_settings()
    if not (
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_verify_service_sid
    ):
        # Dev fallback
        return code == "123456" or (code.isdigit() and len(code) >= 4)

    try:
        from twilio.rest import Client

        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        result = client.verify.v2.services(
            settings.twilio_verify_service_sid
        ).verification_checks.create(to=phone, code=code)
        return result.status == "approved"
    except Exception as err:
        raise PhoneVerifyError(f"Twilio Verify check failed: {err}") from err
