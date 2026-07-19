"""Model selection: hard filter → tier match → reliability-adjusted cost."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

from .registry import MODEL_REGISTRY, TIER_ORDER, CapabilityTier, ModelProfile

if TYPE_CHECKING:
    from .calibration import CalibrationStore
    from .config import RoutingPolicy
    from .registry import PricingCache


class NoEligibleModelError(Exception):
    def __init__(self, tier: CapabilityTier, customer_id: str):
        self.tier = tier
        self.customer_id = customer_id
        super().__init__(
            f"No eligible model for tier={tier.value} customer={customer_id}"
        )


@dataclass
class CircuitBreaker:
    """Trip a provider after N consecutive failures; skip during cooldown."""

    failure_threshold: int = 3
    cooldown_seconds: float = 60.0
    _failures: Dict[str, int] = field(default_factory=dict)
    _tripped_until: Dict[str, float] = field(default_factory=dict)

    def is_tripped(self, provider: str) -> bool:
        until = self._tripped_until.get(provider, 0.0)
        if until and time.time() < until:
            return True
        if until and time.time() >= until:
            self._tripped_until.pop(provider, None)
            self._failures[provider] = 0
        return False

    def record_success(self, provider: str) -> None:
        self._failures[provider] = 0
        self._tripped_until.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        n = self._failures.get(provider, 0) + 1
        self._failures[provider] = n
        if n >= self.failure_threshold:
            self._tripped_until[provider] = time.time() + self.cooldown_seconds


circuit_breaker = CircuitBreaker()


def select_model(
    tier: CapabilityTier,
    *,
    policy: "RoutingPolicy",
    context_tokens: int,
    latency_budget_ms: Optional[int],
    pricing: "PricingCache",
    calibration: "CalibrationStore",
    registry: Optional[tuple] = None,
) -> ModelProfile:
    """Filter → tier-match → minimize reliability-adjusted cost."""
    models = registry if registry is not None else MODEL_REGISTRY
    allowed = policy.allowed_providers()

    # Step 1 — hard constraints (compliance allowlist is absolute)
    candidates: List[ModelProfile] = [
        m
        for m in models
        if m.provider in allowed
        and m.context_window >= context_tokens
        and (latency_budget_ms is None or m.avg_latency_ms <= latency_budget_ms)
        and not circuit_breaker.is_tripped(m.provider)
    ]
    if not candidates:
        raise NoEligibleModelError(tier, policy.customer_id)

    # Step 2 — capability tier match (no under-provisioning)
    min_idx = TIER_ORDER.index(tier)
    matched = [
        m for m in candidates if TIER_ORDER.index(m.capability_tier) >= min_idx
    ]
    matched = matched or list(candidates)

    # Step 3 — reliability-adjusted cost minimization
    def effective_cost(m: ModelProfile) -> float:
        base = pricing.current_cost(m)
        penalty = calibration.reliability_penalty(
            m.model_id, task_category=tier.value
        )
        return base * (1.0 + penalty)

    return min(matched, key=effective_cost)


def select_fast_lane(policy: "RoutingPolicy") -> Optional[ModelProfile]:
    """Autocomplete path — skip classification; still honor circuit breaker."""
    if not policy.fast_lane_provider:
        return None
    for m in MODEL_REGISTRY:
        if m.model_id != policy.fast_lane_provider:
            continue
        if m.provider not in policy.allowed_providers():
            return None
        if circuit_breaker.is_tripped(m.provider):
            return None
        return m
    return None
