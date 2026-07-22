"""Z model router — classify → select → escalate with cost-honest accounting.

Paid-tier feature. Code/prompts pass ephemerally; only anonymized routing
metadata is persisted (see calibration.RoutingOutcomeRecord).
"""

from __future__ import annotations

from .calibration import CalibrationStore, RoutingOutcomeRecord
from .classify import classify_task, estimate_blast_radius, estimate_context_tokens
from .config import RoutingPolicy
from .escalate import RoutingAttempt, run_with_escalation, true_task_cost
from .privacy import ProviderEndpoint
from .registry import (
    MODEL_REGISTRY,
    CapabilityTier,
    ModelProfile,
    PricingCache,
    model_by_id,
    normalize_model_id,
)
from .select import NoEligibleModelError, circuit_breaker, select_model, select_or_prefer
from .task_tier import (
    OPENAI_TIER_FALLBACK,
    bump_tier,
    resolve_capability_tier,
    tier_for_task_mode,
    tier_from_intent_text,
)

__all__ = [
    "CapabilityTier",
    "CalibrationStore",
    "MODEL_REGISTRY",
    "ModelProfile",
    "NoEligibleModelError",
    "OPENAI_TIER_FALLBACK",
    "PricingCache",
    "ProviderEndpoint",
    "RoutingAttempt",
    "RoutingOutcomeRecord",
    "RoutingPolicy",
    "bump_tier",
    "circuit_breaker",
    "classify_task",
    "estimate_blast_radius",
    "estimate_context_tokens",
    "model_by_id",
    "normalize_model_id",
    "resolve_capability_tier",
    "run_with_escalation",
    "select_model",
    "select_or_prefer",
    "tier_for_task_mode",
    "tier_from_intent_text",
    "true_task_cost",
]
