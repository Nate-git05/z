"""Model profile registry + volatility-aware pricing cache.

Adding a model is a data row, not new routing logic — same pattern as
ABSORPTION_TAXONOMY / ESTABLISHED_SOLUTIONS.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Mapping, Optional, Tuple


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
    # OpenAI — primary gateway upstream in V1
    ModelProfile(
        "gpt-4o-mini",
        "openai",
        0.15,
        0.60,
        128_000,
        CapabilityTier.TRIVIAL,
        500,
        ("general",),
    ),
    ModelProfile(
        "gpt-4o",
        "openai",
        2.50,
        10.00,
        128_000,
        CapabilityTier.HARD,
        1200,
        ("general",),
    ),
    ModelProfile(
        "o3-mini",
        "openai",
        1.10,
        4.40,
        200_000,
        CapabilityTier.REASONING_HEAVY,
        2500,
        ("reasoning",),
    ),
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


# Registry model_ids are Z's own naming, not litellm's — this maps the few
# that don't match litellm.model_cost's bare key directly. Update whenever a
# MODEL_REGISTRY row is added/renamed. Intentionally no entry for
# "gemini-1.5-pro" — no live litellm.model_cost entry exists for it; it falls
# back to the static registry row below.
_LITELLM_COST_KEY_ALIASES: Dict[str, str] = {
    "deepseek-v3": "deepseek-chat",
    "groq-llama-70b": "groq/llama-3.3-70b-versatile",
}


def normalize_model_id(model_id: str) -> str:
    """Strip litellm-style ``provider/`` prefix for registry lookup."""
    mid = (model_id or "").strip()
    if not mid:
        return ""
    if "/" in mid:
        provider, rest = mid.split("/", 1)
        if provider in (
            "openai",
            "azure",
            "anthropic",
            "openrouter",
            "google",
            "groq",
            "deepseek",
        ):
            return rest or mid
    return mid


def model_by_id(model_id: str) -> Optional[ModelProfile]:
    mid = normalize_model_id(model_id) or (model_id or "").strip()
    for m in MODEL_REGISTRY:
        if m.model_id == mid or m.model_id == (model_id or "").strip():
            return m
    return None


class PricingCache:
    """Tiered TTL: static rate cards 24h, spot prices 5 minutes.

    Prices come from litellm's bundled ``model_cost`` table (already a
    dependency, refreshed with the package — no network call at read time)
    with the static MODEL_REGISTRY row as a fallback when litellm has no
    matching entry. Tests should inject ``model_cost_table`` explicitly to
    stay hermetic against litellm version upgrades changing real numbers.
    """

    def __init__(self, *, model_cost_table: Optional[Mapping[str, dict]] = None) -> None:
        # model_id -> (cost_in_per_1m, cost_out_per_1m, fetched_at)
        self._cache: Dict[str, Tuple[float, float, float]] = {}
        self._model_cost_table = model_cost_table

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
        """Look up litellm's bundled pricing table; fall back to the static row."""
        static = (model.cost_per_1m_in, model.cost_per_1m_out)
        table = self._model_cost_table
        if table is None:
            try:
                import litellm

                table = litellm.model_cost
            except Exception:
                return static

        key = _LITELLM_COST_KEY_ALIASES.get(model.model_id, model.model_id)
        row = table.get(key)
        if row is None:
            # Defensive fuzzy fallback, scoped to this model's own provider so
            # a bare needle never matches an unrelated vendor's model.
            needle = key.lower()
            for k, v in table.items():
                if not isinstance(v, dict):
                    continue
                if (v.get("litellm_provider") or "").lower() != model.provider:
                    continue
                kl = k.lower()
                if kl == needle or kl.endswith("/" + needle) or kl.endswith("." + needle):
                    row = v
                    break
        if row is None:
            return static

        in_tok = row.get("input_cost_per_token")
        out_tok = row.get("output_cost_per_token")
        if in_tok is None or out_tok is None:
            return static
        return (float(in_tok) * 1_000_000.0, float(out_tok) * 1_000_000.0)
