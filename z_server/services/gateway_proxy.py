"""Upstream provider proxy for the routing gateway (server-held keys only)."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Optional, Tuple

import httpx

ROUTING_POLICY_VERSION = "v0-hardcoded"


def resolve_route(preferred_model: str) -> dict[str, Any]:
    """MVP hardcoded route: preferred model → OpenAI-compatible upstream.

    Later phases call aider.z.routing.select_model with TaskMode.
    """
    model = (preferred_model or "").strip() or "gpt-4o-mini"
    # Strip litellm provider prefix for OpenAI-style upstream when present.
    upstream_model = model
    provider = "openai"
    if "/" in model:
        provider, rest = model.split("/", 1)
        if provider in ("openai", "azure", "openrouter"):
            upstream_model = rest if provider == "openai" else model
        else:
            # anthropic/claude-… — keep full id for Anthropic Messages API later
            upstream_model = model
    return {
        "model_id": model,
        "upstream_model": upstream_model,
        "provider": provider,
        "tier": "default",
        "routing_policy_version": ROUTING_POLICY_VERSION,
    }


def _openai_key() -> Optional[str]:
    return (
        os.environ.get("Z_GATEWAY_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or None
    )


def _stub_enabled() -> bool:
    flag = os.environ.get("Z_GATEWAY_STUB", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    # Dev default: stub when no server-side provider key is configured.
    return (
        os.environ.get("Z_SERVER_DEV", "1").strip().lower()
        in ("1", "true", "yes", "on")
        and not _openai_key()
    )


def stub_completion(model: str, messages: list) -> dict[str, Any]:
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
    text = (
        f"[Z gateway stub] Model `{model}` received your request. "
        f"Upstream keys are not configured on this server yet. "
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
    }


def proxy_chat_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stream: bool = False,
) -> Tuple[dict[str, Any], dict[str, Any]]:
    """Call upstream or stub. Returns (response_body, meta)."""
    route = resolve_route(model)
    meta: dict[str, Any] = {
        "model_id": route["model_id"],
        "tier": route["tier"],
        "routing_policy_version": route["routing_policy_version"],
        "status": "ok",
        "error_message": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "latency_ms": None,
    }
    t0 = time.perf_counter()

    if stream:
        # V0: refuse stream; client can retry non-streaming. Phase 1d+ adds SSE.
        meta["status"] = "error"
        meta["error_message"] = "streaming not implemented in gateway v0"
        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        raise GatewayUpstreamError(400, meta["error_message"], meta)

    key = _openai_key()
    force_stub = os.environ.get("Z_GATEWAY_STUB", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if force_stub or not key:
        if force_stub or _stub_enabled():
            body = stub_completion(route["upstream_model"], messages)
            usage = body.get("usage") or {}
            meta["status"] = "stub"
            meta["input_tokens"] = usage.get("prompt_tokens")
            meta["output_tokens"] = usage.get("completion_tokens")
            meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            return body, meta
        meta["status"] = "upstream_unavailable"
        meta["error_message"] = (
            "Gateway has no provider keys. Set Z_GATEWAY_OPENAI_API_KEY on the server."
        )
        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        raise GatewayUpstreamError(503, meta["error_message"], meta)

    base = (
        os.environ.get("Z_GATEWAY_OPENAI_BASE")
        or os.environ.get("OPENAI_API_BASE")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    payload: dict[str, Any] = {
        "model": route["upstream_model"],
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            meta["status"] = "error"
            meta["error_message"] = resp.text[:500]
            raise GatewayUpstreamError(resp.status_code, meta["error_message"], meta)
        body = resp.json()
        usage = body.get("usage") or {}
        meta["input_tokens"] = usage.get("prompt_tokens")
        meta["output_tokens"] = usage.get("completion_tokens")
        return body, meta
    except GatewayUpstreamError:
        raise
    except Exception as err:
        meta["status"] = "error"
        meta["error_message"] = str(err)[:500]
        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        raise GatewayUpstreamError(502, meta["error_message"], meta) from err


class GatewayUpstreamError(Exception):
    def __init__(self, status_code: int, message: str, meta: dict[str, Any]):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.meta = meta
