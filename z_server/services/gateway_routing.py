"""Phase 5 — TaskMode/Intent → tier selection + escalation on the gateway.

Reuses ``aider.z.routing`` (select_or_prefer, calibration, task_tier). Provider
keys stay on the server; the desktop only sends task_mode / intent hints.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Sequence

ROUTING_POLICY_VERSION = "v1-taskmode"

# Registry provider -> (server-side override env var, generic fallback env var).
# Server-side override lets an operator run the gateway with different keys
# than whatever's in the process's own provider env vars.
_PROVIDER_KEY_ENV = {
    "openai": ("Z_GATEWAY_OPENAI_API_KEY", "OPENAI_API_KEY"),
    "anthropic": ("Z_GATEWAY_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    "google": ("Z_GATEWAY_GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "deepseek": ("Z_GATEWAY_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    "groq": ("Z_GATEWAY_GROQ_API_KEY", "GROQ_API_KEY"),
}


def provider_key(provider: str) -> Optional[str]:
    server_var, generic_var = _PROVIDER_KEY_ENV.get(provider, (None, None))
    if server_var and os.environ.get(server_var):
        return os.environ.get(server_var)
    if generic_var and os.environ.get(generic_var):
        return os.environ.get(generic_var)
    return None


def _openai_key() -> Optional[str]:
    return provider_key("openai")


def _anthropic_key() -> Optional[str]:
    return provider_key("anthropic")


def _any_provider_key() -> bool:
    return any(provider_key(p) for p in _PROVIDER_KEY_ENV)


def _stub_mode() -> bool:
    flag = os.environ.get("Z_GATEWAY_STUB", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return (
        os.environ.get("Z_SERVER_DEV", "1").strip().lower()
        in ("1", "true", "yes", "on")
        and not _any_provider_key()
    )


def stub_mode() -> bool:
    """Public alias — gateway_proxy shares this instead of re-deriving it."""
    return _stub_mode()


def available_providers() -> set[str]:
    """Providers the gateway can actually call (or simulate in stub mode)."""
    if _stub_mode():
        return set(_PROVIDER_KEY_ENV)
    out = {p for p in _PROVIDER_KEY_ENV if provider_key(p)}
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
    domain: Optional[str] = None,
    tier: Optional[str] = None,
    escalate: bool = False,
    escalation_depth: int = 0,
    customer_id: str = "",
    context_tokens: int = 8192,
) -> dict[str, Any]:
    """Select model_id + tier using TaskMode/Intent/Domain policy.

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
    from aider.z.routing.classify import domain_from_text
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

    # Older clients that don't send a domain hint still get one derived from
    # whatever intent text is available server-side.
    resolved_domain = (domain or "").strip().lower() or domain_from_text(intent_text)

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
            domain=resolved_domain,
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
                "domain": resolved_domain,
                "preferred_model": preferred,
                "routing_policy_version": ROUTING_POLICY_VERSION,
                "selection": "openai_fallback",
            }

    # Remap to a callable upstream when the selected provider has no key —
    # uniform across all providers (not just openai/anthropic special-cased).
    provider = profile.provider
    upstream_model = profile.model_id
    selection = "policy"
    if not _stub_mode() and not provider_key(provider):
        provider = "openai"
        upstream_model = OPENAI_TIER_FALLBACK[final_tier]
        selection = "provider_remap" if _openai_key() else "no_key_fallback"

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
        "domain": resolved_domain,
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
