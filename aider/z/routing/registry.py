"""Model profile registry + volatility-aware pricing cache.

Adding a model is a data row, not new routing logic — same pattern as
ABSORPTION_TAXONOMY / ESTABLISHED_SOLUTIONS.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple


class CapabilityTier(str, Enum):
    TRIVIAL = "trivial"  # rename, formatting, boilerplate
    MODERATE = "moderate"  # single-file bugfix, small feature
    HARD = "hard"  # multi-file, race conditions, migrations
    REASONING_HEAVY = "reasoning_heavy"  # architecture, ambiguous specs


TIER_ORDER = (
    CapabilityTier.TRIVIAL,
    CapabilityTier.MODERATE,
    CapabilityTier.HARD,
    CapabilityTier.REASONING_HEAVY,
)


@dataclass(frozen=True)
class ModelProfile:
    """One row per (model, provider)."""

    model_id: str
    provider: str
    cost_per_1m_in: float
    cost_per_1m_out: float
    context_window: int
    capability_tier: CapabilityTier
    avg_latency_ms: int
    specialty_tags: Tuple[str, ...] = ()
    pricing_volatility: str = "static"  # "static" | "spot"


MODEL_REGISTRY: Tuple[ModelProfile, ...] = (
    ModelProfile(
        "claude-sonnet-5",
        "anthropic",
        3.00,
        15.00,
        200_000,
        CapabilityTier.HARD,
        1800,
        ("reasoning",),
    ),
    ModelProfile(
        "claude-opus-4-8",
        "anthropic",
        15.00,
        75.00,
        200_000,
        CapabilityTier.REASONING_HEAVY,
        3000,
        ("reasoning",),
    ),
    ModelProfile(
        "claude-haiku-4-5",
        "anthropic",
        1.00,
        5.00,
        200_000,
        CapabilityTier.MODERATE,
        600,
        (),
    ),
    ModelProfile(
        "deepseek-v3",
        "deepseek",
        0.27,
        1.10,
        64_000,
        CapabilityTier.TRIVIAL,
        900,
        (),
    ),
    ModelProfile(
        "gemini-1.5-pro",
        "google",
        1.25,
        5.00,
        2_000_000,
        CapabilityTier.MODERATE,
        1500,
        ("long_context",),
    ),
    ModelProfile(
        "groq-llama-70b",
        "groq",
        0.59,
        0.79,
        8_000,
        CapabilityTier.TRIVIAL,
        80,
        ("low_latency",),
        pricing_volatility="static",
    ),
)


def model_by_id(model_id: str) -> Optional[ModelProfile]:
    for m in MODEL_REGISTRY:
        if m.model_id == model_id:
            return m
    return None


class PricingCache:
    """Tiered TTL: static rate cards 24h, spot prices 5 minutes."""

    def __init__(self) -> None:
        # model_id -> (cost_in_per_1m, cost_out_per_1m, fetched_at)
        self._cache: Dict[str, Tuple[float, float, float]] = {}

    def current_cost(self, model: ModelProfile) -> float:
        """Return a comparable unit cost (input rate) for minimization."""
        ttl = 300 if model.pricing_volatility == "spot" else 60 * 60 * 24
        cached = self._cache.get(model.model_id)
        if cached and (time.time() - cached[2]) < ttl:
            return cached[0]
        price = self._fetch_price(model)
        self._cache[model.model_id] = (*price, time.time())
        return price[0]

    def estimate_call_cost(
        self, model: ModelProfile, *, tokens_in: int, tokens_out: int
    ) -> float:
        in_rate, out_rate, _ = self._ensure(model)
        return (tokens_in / 1_000_000.0) * in_rate + (
            tokens_out / 1_000_000.0
        ) * out_rate

    def _ensure(self, model: ModelProfile) -> Tuple[float, float, float]:
        self.current_cost(model)
        return self._cache[model.model_id]

    def _fetch_price(self, model: ModelProfile) -> Tuple[float, float]:
        """Provider-specific fetcher — defaults to the static registry row."""
        return (model.cost_per_1m_in, model.cost_per_1m_out)
