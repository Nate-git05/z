"""Phase 5 — TaskMode/Intent → tier selection + escalation on the gateway.

Reuses ``aider.z.routing`` (select_or_prefer, calibration, task_tier). Provider
keys stay on the server; the desktop only sends task_mode / intent hints.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Sequence

ROUTING_POLICY_VERSION = "v1-taskmode"


def _openai_key() -> Optional[str]:
    return (
        os.environ.get("Z_GATEWAY_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or None
    )


def _anthropic_key() -> Optional[str]:
    return (
        os.environ.get("Z_GATEWAY_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or None
    )


def _stub_mode() -> bool:
    flag = os.environ.get("Z_GATEWAY_STUB", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return (
        os.environ.get("Z_SERVER_DEV", "1").strip().lower()
        in ("1", "true", "yes", "on")
        and not _openai_key()
        and not _anthropic_key()
    )


def available_providers() -> set[str]:
    """Providers the gateway can actually call (or simulate in stub mode)."""
    if _stub_mode():
        return {"openai", "anthropic", "google", "groq", "deepseek"}
    out: set[str] = set()
    if _openai_key():
        out.add("openai")
    if _anthropic_key():
        out.add("anthropic")
    if not out:
        out.add("openai")
    return out


def _last_user_text(messages: Sequence[Any]) -> str:
    for m in reversed(list(messages or ())):
        if isinstance(m, dict):
            role, content = m.get("role"), m.get("content")
        else:
            role = getattr(m, "role", None)
            content = getattr(m, "content", None)
        if role != "user":
            continue
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict):
                    parts.append(str(p.get("text", p)))
                else:
                    parts.append(str(p))
            content = " ".join(parts)
        text = str(content or "").strip()
        if text:
            return text[:4000]
    return ""


def _calibration(customer_id: str):
    from aider.z.routing import CalibrationStore

    base = Path(os.environ.get("Z_HOME", Path.home() / ".z"))
    path = base / "routing" / "gateway_calibration.json"
    return CalibrationStore(path=path, customer_id=customer_id or "gateway")


def _policy(customer_id: str, providers: set[str]):
    from aider.z.routing import ProviderEndpoint, RoutingPolicy

    endpoints = tuple(
        ProviderEndpoint(
            provider=p,
            base_url=f"gateway://{p}",
            auth_ref=f"server:{p}",
        )
        for p in sorted(providers)
    )
    max_esc = int(os.environ.get("Z_GATEWAY_MAX_ESCALATIONS", "2") or "2")
    return RoutingPolicy(
        customer_id=customer_id or "gateway",
        allowed_endpoints=endpoints,
        max_escalations=max(0, max_esc),
    )


def resolve_policy_route(
    *,
    preferred_model: str,
    messages: Optional[Sequence[Any]] = None,
    task_mode: Optional[str] = None,
    intent: Optional[str] = None,
    tier: Optional[str] = None,
    escalate: bool = False,
    escalation_depth: int = 0,
    customer_id: str = "",
    context_tokens: int = 8192,
) -> dict[str, Any]:
    """Select model_id + tier using TaskMode/Intent policy.

    Returns a route dict consumed by ``gateway_proxy``.
    """
    from aider.z.routing import (
        OPENAI_TIER_FALLBACK,
        PricingCache,
        bump_tier,
        model_by_id,
        normalize_model_id,
        resolve_capability_tier,
        select_or_prefer,
    )
    from aider.z.routing.select import NoEligibleModelError

    messages = messages or []
    intent_text = (intent or "").strip() or _last_user_text(messages)
    base_tier = resolve_capability_tier(
        task_mode=task_mode,
        intent=intent_text,
        tier_hint=tier,
    )
    depth = max(0, int(escalation_depth or 0))
    if escalate and depth == 0:
        depth = 1
    final_tier = bump_tier(base_tier, depth)
    escalated = depth > 0 or final_tier != base_tier

    preferred = normalize_model_id(preferred_model) or (preferred_model or "").strip()
    if not preferred:
        preferred = OPENAI_TIER_FALLBACK[final_tier]

    providers = available_providers()
    policy = _policy(customer_id, providers)
    calibration = _calibration(customer_id)
    pricing = PricingCache()

    try:
        profile = select_or_prefer(
            final_tier,
            preferred,
            policy=policy,
            context_tokens=max(1, int(context_tokens or 8192)),
            latency_budget_ms=None,
            pricing=pricing,
            calibration=calibration,
        )
    except NoEligibleModelError:
        fallback_id = OPENAI_TIER_FALLBACK[final_tier]
        profile = model_by_id(fallback_id)
        if profile is None:
            return {
                "model_id": fallback_id,
                "upstream_model": fallback_id,
                "provider": "openai",
                "tier": final_tier.value,
                "base_tier": base_tier.value,
                "escalated": escalated,
                "escalation_depth": depth,
                "task_mode": task_mode,
                "preferred_model": preferred,
                "routing_policy_version": ROUTING_POLICY_VERSION,
                "selection": "openai_fallback",
            }

    # Remap to a callable upstream when the selected provider has no key.
    provider = profile.provider
    upstream_model = profile.model_id
    selection = "policy"
    if not _stub_mode():
        if provider == "openai" and not _openai_key():
            provider, upstream_model, selection = "openai", OPENAI_TIER_FALLBACK[final_tier], "no_key_fallback"
        elif provider == "anthropic" and not _anthropic_key():
            if _openai_key():
                provider = "openai"
                upstream_model = OPENAI_TIER_FALLBACK[final_tier]
                selection = "provider_remap"
            else:
                provider = "openai"
                upstream_model = OPENAI_TIER_FALLBACK[final_tier]
                selection = "no_key_fallback"

    # Preferred model id for logging (may differ from upstream after remap)
    model_id = profile.model_id
    if selection != "policy":
        model_id = upstream_model

    return {
        "model_id": model_id,
        "upstream_model": upstream_model,
        "provider": provider,
        "tier": final_tier.value,
        "base_tier": base_tier.value,
        "escalated": escalated,
        "escalation_depth": depth,
        "task_mode": (task_mode or None),
        "preferred_model": preferred,
        "routing_policy_version": ROUTING_POLICY_VERSION,
        "selection": selection,
        "capability_tier_ok": True,
    }


def record_gateway_outcome(
    *,
    model_id: str,
    tier: str,
    gate_passed: bool,
    escalated: bool = False,
    cost_usd: float = 0.0,
    customer_id: str = "",
    checker_triggered: Optional[str] = None,
) -> dict[str, Any]:
    """Calibration hook — anonymized pass/fail for reliability-adjusted select."""
    store = _calibration(customer_id)
    rec = store.record_outcome(
        model_id=model_id,
        task_category=tier or "moderate",
        gate_passed=bool(gate_passed),
        escalated=bool(escalated),
        checker_triggered=checker_triggered,
        cost_usd=float(cost_usd or 0.0),
        customer_id=customer_id or "gateway",
    )
    return {
        "ok": True,
        "model_id": rec.model_id,
        "task_category": rec.task_category,
        "gate_passed": rec.gate_passed,
        "escalated": rec.escalated,
        "routing_policy_version": ROUTING_POLICY_VERSION,
    }


def record_attempt_success(
    *,
    model_id: str,
    tier: str,
    escalated: bool,
    customer_id: str,
    cost_usd: float = 0.0,
) -> None:
    """Best-effort calibration after a successful gateway completion."""
    try:
        record_gateway_outcome(
            model_id=model_id,
            tier=tier,
            gate_passed=True,
            escalated=escalated,
            cost_usd=cost_usd,
            customer_id=customer_id,
        )
    except Exception:
        pass


def record_attempt_failure(
    *,
    model_id: str,
    tier: str,
    escalated: bool,
    customer_id: str,
    checker_triggered: Optional[str] = None,
) -> None:
    try:
        record_gateway_outcome(
            model_id=model_id,
            tier=tier,
            gate_passed=False,
            escalated=escalated,
            customer_id=customer_id,
            checker_triggered=checker_triggered,
        )
    except Exception:
        pass
