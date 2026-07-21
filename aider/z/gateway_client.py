"""Client helpers to send model traffic through the Z routing gateway.

When ``auth_mode=router`` and the user has a Z access token, point OpenAI-
compatible litellm traffic at ``{Z_API}/v1/gateway`` using the Z session token
as ``OPENAI_API_KEY``. Provider keys never need to live on the desktop.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple


def gateway_enabled() -> bool:
    flag = os.environ.get("Z_USE_GATEWAY", "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def gateway_base_url() -> str:
    from aider.z.auth import get_auth_base_url

    return f"{get_auth_base_url().rstrip('/')}/v1/gateway"


def _access_token() -> Optional[str]:
    try:
        from aider.z.auth import current_session

        creds = current_session()
        if creds and getattr(creds, "access_token", None):
            return creds.access_token
    except Exception:
        pass
    return os.environ.get("Z_ACCESS_TOKEN") or None


def openai_compatible_model(model_id: str) -> str:
    """Rewrite preferred model id so litellm treats it as OpenAI-compatible.

    Gateway receives the original id in the request body ``model`` field when
    we keep a simple ``openai/...`` form; for unknown providers we still force
    the openai/ prefix so traffic hits OPENAI_API_BASE (our gateway).
    """
    mid = (model_id or "").strip()
    if not mid:
        return "openai/gpt-4o-mini"
    if mid.startswith("openai/"):
        return mid
    # Prefer preserving the bare id under openai/ so gateway can re-route.
    if "/" in mid:
        _provider, rest = mid.split("/", 1)
        return f"openai/{rest}"
    return f"openai/{mid}"


def apply_gateway_env_for_router(
    *,
    selected_model: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Configure process env for gateway-backed router mode.

    Returns ``(applied, rewritten_model_or_None)``.
    """
    if not gateway_enabled():
        return False, None
    token = _access_token()
    if not token:
        return False, None

    base = gateway_base_url()
    os.environ["OPENAI_API_BASE"] = base
    os.environ["OPENAI_API_KEY"] = token
    # Avoid accidental direct provider use when gateway is active.
    os.environ["Z_GATEWAY_ACTIVE"] = "1"

    rewritten = None
    if selected_model:
        rewritten = openai_compatible_model(selected_model)
        # Hint for callers that inject --model
        os.environ["Z_GATEWAY_MODEL"] = rewritten
    return True, rewritten


def router_uses_gateway() -> bool:
    return os.environ.get("Z_GATEWAY_ACTIVE", "").strip() == "1"
