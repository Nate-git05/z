"""Application-level encryption for secrets at rest (Fernet via Z_SECRET_KEY)."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from z_server.config import get_settings


def _fernet() -> Fernet:
    """Derive a stable Fernet key from Z_SECRET_KEY (url-safe 32-byte)."""
    settings = get_settings()
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_credentials(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _fernet().encrypt(raw).decode("utf-8")


def decrypt_credentials(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        raw = _fernet().decrypt(token.encode("utf-8"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (InvalidToken, json.JSONDecodeError, TypeError, ValueError):
        return {}
