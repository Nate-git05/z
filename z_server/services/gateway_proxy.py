"""Upstream provider proxy for the routing gateway (server-held keys only)."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Optional, Tuple

from aider.z.routing import PricingCache, model_by_id
from z_server.services.gateway_routing import (
    ROUTING_POLICY_VERSION,
    provider_key,
    record_attempt_failure,
    record_attempt_success,
    resolve_policy_route,
    stub_mode,
)

# litellm's provider prefix, when it differs from the registry's own provider
# string (openai needs no prefix — bare model id already resolves correctly).
_LITELLM_PROVIDER_PREFIX = {
    "anthropic": "anthropic",
    "google": "gemini",
    "deepseek": "deepseek",
    "groq": "groq",
}

# Back-compat alias (tests / older imports)
# v0-hardcoded → v1-taskmode

# Single source of truth for pricing — aider.z.routing.PricingCache (backed by
# litellm's bundled cost table, falling back to the static MODEL_REGISTRY row).
# Replaces the old hand-maintained _PRICE_PER_MTOK dict, which had silently
# drifted out of sync with the registry (e.g. different spellings for the
# same model, several rows never actually matched).
_PRICING = PricingCache()

# Default rate when the selected model isn't in MODEL_REGISTRY at all (e.g. a
# raw preferred_model string that bypassed selection entirely).
_UNKNOWN_MODEL_RATE = (1.0, 4.0)


def _estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    profile = model_by_id(model_id or "")
    if profile is None:
        in_rate, out_rate = _UNKNOWN_MODEL_RATE
        return round(
            (max(0, input_tokens) / 1_000_000.0) * in_rate
            + (max(0, output_tokens) / 1_000_000.0) * out_rate,
            6,
        )
    return round(
        _PRICING.estimate_call_cost(
            profile,
            tokens_in=max(0, input_tokens),
            tokens_out=max(0, output_tokens),
        ),
        6,
    )


def resolve_route(
    preferred_model: str,
    *,
    messages: Optional[list] = None,
    task_mode: Optional[str] = None,
    intent: Optional[str] = None,
    domain: Optional[str] = None,
    tier: Optional[str] = None,
    escalate: bool = False,
    escalation_depth: int = 0,
    customer_id: str = "",
) -> dict[str, Any]:
    """TaskMode-aware route selection (Phase 5). Falls back to preferred id."""
    try:
        return resolve_policy_route(
            preferred_model=preferred_model,
            messages=messages or [],
            task_mode=task_mode,
            intent=intent,
            domain=domain,
            tier=tier,
            escalate=escalate,
            escalation_depth=escalation_depth,
            customer_id=customer_id,
        )
    except Exception:
        # Never fail open without a route — preserve v0 behavior.
        model = (preferred_model or "").strip() or "gpt-4o-mini"
        upstream_model = model
        provider = "openai"
        if "/" in model:
            provider, rest = model.split("/", 1)
            if provider in ("openai", "azure", "openrouter"):
                upstream_model = rest if provider == "openai" else model
            else:
                upstream_model = model
        return {
            "model_id": model,
            "upstream_model": upstream_model,
            "provider": provider,
            "tier": tier or "default",
            "routing_policy_version": ROUTING_POLICY_VERSION,
            "escalated": False,
            "escalation_depth": 0,
            "selection": "legacy_fallback",
        }


def _openai_key() -> Optional[str]:
    return provider_key("openai")


def _stub_enabled() -> bool:
    # Shared with gateway_routing.available_providers() so the two never
    # disagree about whether the gateway is in stub mode.
    return stub_mode()


def stub_completion(model: str, messages: list, *, route: Optional[dict] = None) -> dict[str, Any]:
    """Dev/test completion when no upstream key is configured."""
    last_user = ""
    for m in reversed(messages or []):
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if isinstance(content, list):
            content = " ".join(
                str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in content
            )
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        if role == "user" and content:
            last_user = str(content)[:200]
            break
    tier = (route or {}).get("tier") or "default"
    policy = (route or {}).get("routing_policy_version") or ROUTING_POLICY_VERSION
    text = (
        f"[Z gateway stub] Model `{model}` (tier={tier}, policy={policy}) "
        f"received your request. Upstream keys are not configured on this server yet. "
        f"Last user excerpt: {last_user!r}"
    )
    return {
        "id": f"chatcmpl-stub-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": max(1, len(json.dumps(messages)) // 4),
            "completion_tokens": max(1, len(text) // 4),
            "total_tokens": 0,
        },
        "z_routing": {
            "model_id": (route or {}).get("model_id") or model,
            "tier": tier,
            "base_tier": (route or {}).get("base_tier"),
            "escalated": bool((route or {}).get("escalated")),
            "escalation_depth": (route or {}).get("escalation_depth") or 0,
            "task_mode": (route or {}).get("task_mode"),
            "routing_policy_version": policy,
            "selection": (route or {}).get("selection"),
        },
    }


def _meta_from_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_id": route.get("model_id"),
        "tier": route.get("tier"),
        "base_tier": route.get("base_tier"),
        "escalated": bool(route.get("escalated")),
        "escalation_depth": int(route.get("escalation_depth") or 0),
        "task_mode": route.get("task_mode"),
        "routing_policy_version": route.get("routing_policy_version")
        or ROUTING_POLICY_VERSION,
        "selection": route.get("selection"),
        "status": "ok",
        "error_message": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "latency_ms": None,
    }


def _call_openai_upstream(
    *,
    upstream_model: str,
    messages: list[dict[str, Any]],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> dict[str, Any]:
    import httpx

    key = _openai_key()
    if not key:
        raise GatewayUpstreamError(
            503,
            "Gateway has no provider keys. Set Z_GATEWAY_OPENAI_API_KEY on the server.",
            {"status": "upstream_unavailable"},
        )
    base = (
        os.environ.get("Z_GATEWAY_OPENAI_BASE")
        or os.environ.get("OPENAI_API_BASE")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    # Avoid looping into ourselves when OPENAI_API_BASE points at the gateway.
    if "/v1/gateway" in base:
        base = "https://api.openai.com/v1"
    payload: dict[str, Any] = {
        "model": upstream_model,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code >= 400:
        raise GatewayUpstreamError(
            resp.status_code,
            resp.text[:500],
            {"status": "error", "error_message": resp.text[:500]},
        )
    return resp.json()


def _call_upstream_via_litellm(
    *,
    provider: str,
    upstream_model: str,
    messages: list[dict[str, Any]],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> dict[str, Any]:
    """Anthropic/Google/DeepSeek/Groq — litellm already normalizes these to
    an OpenAI-compatible response shape, so one function covers all four
    instead of a bespoke HTTP integration per vendor."""
    import litellm

    key = provider_key(provider)
    if not key:
        raise GatewayUpstreamError(
            503,
            f"Gateway has no {provider} key. Set Z_GATEWAY_{provider.upper()}_API_KEY "
            "on the server.",
            {"status": "upstream_unavailable"},
        )
    prefix = _LITELLM_PROVIDER_PREFIX.get(provider, provider)
    litellm_model = f"{prefix}/{upstream_model}"
    kwargs: dict[str, Any] = {
        "model": litellm_model,
        "messages": messages,
        "api_key": key,
        "timeout": 120.0,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    try:
        resp = litellm.completion(**kwargs)
    except Exception as err:
        status = int(getattr(err, "status_code", 0) or 502)
        raise GatewayUpstreamError(
            status,
            str(err)[:500],
            {"status": "error", "error_message": str(err)[:500]},
        ) from err

    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    if hasattr(resp, "to_dict"):
        return resp.to_dict()
    return dict(resp)


def _call_upstream(
    *,
    provider: str,
    upstream_model: str,
    messages: list[dict[str, Any]],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> dict[str, Any]:
    """Dispatch to the right provider integration."""
    if provider == "openai":
        return _call_openai_upstream(
            upstream_model=upstream_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    return _call_upstream_via_litellm(
        provider=provider,
        upstream_model=upstream_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def proxy_chat_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stream: bool = False,
    task_mode: Optional[str] = None,
    intent: Optional[str] = None,
    domain: Optional[str] = None,
    tier: Optional[str] = None,
    escalate: bool = False,
    escalation_depth: int = 0,
    customer_id: str = "",
    auto_escalate_on_upstream_error: bool = True,
) -> Tuple[dict[str, Any], dict[str, Any]]:
    """Call upstream or stub. Returns (response_body, meta)."""
    route = resolve_route(
        model,
        messages=messages,
        task_mode=task_mode,
        intent=intent,
        domain=domain,
        tier=tier,
        escalate=escalate,
        escalation_depth=escalation_depth,
        customer_id=customer_id,
    )
    meta = _meta_from_route(route)
    t0 = time.perf_counter()

    if stream:
        meta["status"] = "error"
        meta["error_message"] = "streaming not implemented in gateway v0"
        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        raise GatewayUpstreamError(400, meta["error_message"], meta)

    # route's provider is already remapped to one with a real key (or "openai"
    # as the universal fallback) by resolve_policy_route — see gateway_routing.
    key = provider_key(route["provider"])
    force_stub = os.environ.get("Z_GATEWAY_STUB", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    def _finish_ok(body: dict[str, Any], status: str) -> Tuple[dict[str, Any], dict[str, Any]]:
        usage = body.get("usage") or {}
        meta["status"] = status
        in_tok = usage.get("prompt_tokens")
        out_tok = usage.get("completion_tokens")
        meta["input_tokens"] = in_tok
        meta["output_tokens"] = out_tok
        # Phase 14b — estimate cost when provider omits it
        if meta.get("cost_usd") is None:
            meta["cost_usd"] = _estimate_cost_usd(
                str(meta.get("model_id") or route.get("upstream_model") or ""),
                int(in_tok or 0),
                int(out_tok or 0),
            )
        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        body = dict(body)
        body["z_routing"] = {
            "model_id": meta.get("model_id"),
            "tier": meta.get("tier"),
            "base_tier": meta.get("base_tier"),
            "escalated": meta.get("escalated"),
            "escalation_depth": meta.get("escalation_depth"),
            "task_mode": meta.get("task_mode") or task_mode,
            "routing_policy_version": meta.get("routing_policy_version"),
            "selection": meta.get("selection"),
        }
        record_attempt_success(
            model_id=str(meta.get("model_id") or route["upstream_model"]),
            tier=str(meta.get("tier") or "moderate"),
            escalated=bool(meta.get("escalated")),
            customer_id=customer_id,
        )
        return body, meta

    if force_stub or not key:
        if force_stub or _stub_enabled():
            body = stub_completion(route["upstream_model"], messages, route=route)
            return _finish_ok(body, "stub")
        meta["status"] = "upstream_unavailable"
        meta["error_message"] = (
            f"Gateway has no {route['provider']} key. Set "
            f"Z_GATEWAY_{route['provider'].upper()}_API_KEY on the server."
        )
        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        raise GatewayUpstreamError(503, meta["error_message"], meta)

    try:
        body = _call_upstream(
            provider=route["provider"],
            upstream_model=route["upstream_model"],
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _finish_ok(body, "ok")
    except GatewayUpstreamError as err:
        # Phase 5b — one automatic escalate retry on upstream 429/5xx.
        can_auto = (
            auto_escalate_on_upstream_error
            and err.status_code in (429, 500, 502, 503)
            and int(route.get("escalation_depth") or 0) < 2
        )
        if can_auto:
            retry_route = resolve_route(
                model,
                messages=messages,
                task_mode=task_mode,
                intent=intent,
                domain=domain,
                tier=tier,
                escalate=True,
                escalation_depth=int(route.get("escalation_depth") or 0) + 1,
                customer_id=customer_id,
            )
            meta = _meta_from_route(retry_route)
            meta["auto_escalated"] = True
            try:
                body = _call_upstream(
                    provider=retry_route["provider"],
                    upstream_model=retry_route["upstream_model"],
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return _finish_ok(body, "ok")
            except GatewayUpstreamError as err2:
                meta.update(err2.meta or {})
                meta["status"] = meta.get("status") or "error"
                meta["error_message"] = err2.message
                meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                record_attempt_failure(
                    model_id=str(meta.get("model_id") or retry_route["upstream_model"]),
                    tier=str(meta.get("tier") or "moderate"),
                    escalated=True,
                    customer_id=customer_id,
                    checker_triggered=f"upstream_{err2.status_code}",
                )
                raise GatewayUpstreamError(err2.status_code, err2.message, meta) from err2

        meta.update(err.meta or {})
        meta["status"] = meta.get("status") or "error"
        meta["error_message"] = err.message
        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        record_attempt_failure(
            model_id=str(meta.get("model_id") or route["upstream_model"]),
            tier=str(meta.get("tier") or "moderate"),
            escalated=bool(meta.get("escalated")),
            customer_id=customer_id,
            checker_triggered=f"upstream_{err.status_code}",
        )
        raise GatewayUpstreamError(err.status_code, err.message, meta) from err
    except Exception as err:
        meta["status"] = "error"
        meta["error_message"] = str(err)[:500]
        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        record_attempt_failure(
            model_id=str(meta.get("model_id") or route["upstream_model"]),
            tier=str(meta.get("tier") or "moderate"),
            escalated=bool(meta.get("escalated")),
            customer_id=customer_id,
            checker_triggered="exception",
        )
        raise GatewayUpstreamError(502, meta["error_message"], meta) from err


class GatewayUpstreamError(Exception):
    def __init__(self, status_code: int, message: str, meta: dict[str, Any]):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.meta = meta
