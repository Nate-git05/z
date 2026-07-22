"""Client helpers to send model traffic through the Z routing gateway.

When ``auth_mode=router`` and the user has a Z access token, point OpenAI-
compatible litellm traffic at ``{Z_API}/v1/gateway`` using the Z session token
as ``OPENAI_API_KEY``. Provider keys never need to live on the desktop.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple, Union


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


def set_gateway_routing_hints(
    *,
    task_mode: Optional[Union[str, object]] = None,
    intent: Optional[str] = None,
    escalate: bool = False,
    escalation_depth: int = 0,
    thread_id: Optional[str] = None,
) -> None:
    """Publish per-turn routing hints for the next completion call."""
    if task_mode is not None:
        value = getattr(task_mode, "value", task_mode)
        os.environ["Z_GATEWAY_TASK_MODE"] = str(value)
    elif "Z_GATEWAY_TASK_MODE" in os.environ:
        os.environ.pop("Z_GATEWAY_TASK_MODE", None)

    if intent:
        os.environ["Z_GATEWAY_INTENT"] = str(intent)[:2000]
    else:
        os.environ.pop("Z_GATEWAY_INTENT", None)

    if escalate:
        os.environ["Z_GATEWAY_ESCALATE"] = "1"
    else:
        os.environ.pop("Z_GATEWAY_ESCALATE", None)

    if escalation_depth:
        os.environ["Z_GATEWAY_ESCALATION_DEPTH"] = str(int(escalation_depth))
    else:
        os.environ.pop("Z_GATEWAY_ESCALATION_DEPTH", None)

    if thread_id:
        os.environ["Z_GATEWAY_THREAD_ID"] = str(thread_id)


def gateway_routing_extra_body() -> Dict[str, Any]:
    """Build OpenAI-compatible extra_body fields for the gateway."""
    body: Dict[str, Any] = {}
    mode = os.environ.get("Z_GATEWAY_TASK_MODE")
    if mode:
        body["task_mode"] = mode
    intent = os.environ.get("Z_GATEWAY_INTENT")
    if intent:
        body["intent"] = intent
    if os.environ.get("Z_GATEWAY_ESCALATE", "").strip() in ("1", "true", "yes"):
        body["escalate"] = True
    depth = os.environ.get("Z_GATEWAY_ESCALATION_DEPTH", "").strip()
    if depth.isdigit() and int(depth) > 0:
        body["escalation_depth"] = int(depth)
    thread = os.environ.get("Z_GATEWAY_THREAD_ID")
    if thread:
        body["thread_id"] = thread
    return body


def inject_gateway_routing_into_model(model) -> None:
    """Merge routing hints into ``model.extra_params['extra_body']`` when active."""
    if not router_uses_gateway() or model is None:
        return
    extra = gateway_routing_extra_body()
    if not extra:
        return
    if not getattr(model, "extra_params", None):
        model.extra_params = {}
    body = dict(model.extra_params.get("extra_body") or {})
    body.update(extra)
    model.extra_params["extra_body"] = body


def report_routing_outcome(
    *,
    model_id: str,
    tier: str,
    gate_passed: bool,
    escalated: bool = False,
    cost_usd: float = 0.0,
    checker_triggered: Optional[str] = None,
) -> bool:
    """Best-effort POST /v1/gateway/routing/outcome for calibration."""
    if not router_uses_gateway() or not gateway_enabled():
        return False
    token = _access_token()
    if not token:
        return False
    try:
        import httpx

        url = f"{gateway_base_url().rstrip('/')}/routing/outcome"
        resp = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "model_id": model_id,
                "tier": tier,
                "gate_passed": bool(gate_passed),
                "escalated": bool(escalated),
                "cost_usd": float(cost_usd or 0.0),
                "checker_triggered": checker_triggered,
            },
            timeout=10.0,
        )
        return resp.status_code < 400
    except Exception:
        return False
