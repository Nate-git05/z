"""Local, network-free model routing for multi-key BYOK mode.

Plays the same role gateway_client.py plays for router mode — reuses
aider.z.routing's classify -> tier -> select pipeline — but resolves a model
id entirely locally and never talks to a Z server. Calibration persists to
the same ``~/.z/routing/calibration.json`` CalibrationStore already uses
under a dedicated "local-byok" customer id, kept segregated from anything
server-side. There is no network call anywhere in this module by
construction: litellm.model_cost is an in-process dict, and CalibrationStore
only writes to local disk.
"""

from __future__ import annotations

import os
from typing import Optional, Union

# Registry provider string -> canonical env var name (mirrors
# aider/models.py's fast_validate_environment keymap). Note the registry's
# "google" maps to litellm/aider's GEMINI_API_KEY, not a GOOGLE_* var.
_PROVIDER_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "google": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}

_LOCAL_BYOK_CUSTOMER_ID = "local-byok"


def configured_byok_providers() -> set[str]:
    """Registry providers with a non-empty key in the current process env.

    Call after ``_load_byok_env()`` has run (the CLI already guarantees this
    at startup) — this is a pure env inspection, no file I/O of its own.
    """
    return {p for p, var in _PROVIDER_ENV_VAR.items() if (os.environ.get(var) or "").strip()}


def select_local_model(
    *,
    task_mode: Optional[Union[str, object]] = None,
    intent: Optional[str] = None,
    domain: Optional[str] = None,
    preferred_model_id: Optional[str] = None,
    context_tokens: int = 4096,
    escalation_depth: int = 0,
) -> Optional[str]:
    """Resolve a model_id from the user's own configured BYOK providers.

    Returns ``None`` when zero providers are configured, or when nothing is
    eligible — callers should fall back to the single saved
    ``config.selected_model`` (today's behavior) in that case.
    """
    providers = configured_byok_providers()
    if not providers:
        return None

    from aider.z.routing import (
        CalibrationStore,
        PricingCache,
        ProviderEndpoint,
        RoutingPolicy,
        bump_tier,
        resolve_capability_tier,
        select_or_prefer,
    )
    from aider.z.routing.classify import domain_from_text
    from aider.z.routing.select import NoEligibleModelError

    resolved_domain = domain or domain_from_text(intent)
    tier = resolve_capability_tier(task_mode=task_mode, intent=intent)
    tier = bump_tier(tier, escalation_depth)

    endpoints = tuple(
        ProviderEndpoint(provider=p, base_url=f"byok://{p}", auth_ref=f"env:{_PROVIDER_ENV_VAR[p]}")
        for p in sorted(providers)
    )
    policy = RoutingPolicy(customer_id=_LOCAL_BYOK_CUSTOMER_ID, allowed_endpoints=endpoints)
    calibration = CalibrationStore(customer_id=_LOCAL_BYOK_CUSTOMER_ID)
    pricing = PricingCache()

    try:
        profile = select_or_prefer(
            tier,
            preferred_model_id,
            policy=policy,
            context_tokens=context_tokens,
            latency_budget_ms=None,
            pricing=pricing,
            calibration=calibration,
            domain=resolved_domain,
        )
    except NoEligibleModelError:
        return None
    return profile.model_id


def record_local_outcome(
    *,
    model_id: str,
    tier: str,
    gate_passed: bool,
    escalated: bool = False,
    cost_usd: float = 0.0,
) -> None:
    """Local-only calibration write — never leaves disk, never hits a server."""
    from aider.z.routing import CalibrationStore

    CalibrationStore(customer_id=_LOCAL_BYOK_CUSTOMER_ID).record_outcome(
        model_id=model_id,
        task_category=tier or "moderate",
        gate_passed=bool(gate_passed),
        escalated=bool(escalated),
        cost_usd=float(cost_usd or 0.0),
        customer_id=_LOCAL_BYOK_CUSTOMER_ID,
    )
