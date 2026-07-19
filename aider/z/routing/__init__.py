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
)
from .select import NoEligibleModelError, circuit_breaker, select_model

__all__ = [
    "CapabilityTier",
    "CalibrationStore",
    "MODEL_REGISTRY",
    "ModelProfile",
    "NoEligibleModelError",
    "PricingCache",
    "ProviderEndpoint",
    "RoutingAttempt",
    "RoutingOutcomeRecord",
    "RoutingPolicy",
    "circuit_breaker",
    "classify_task",
    "estimate_blast_radius",
    "estimate_context_tokens",
    "model_by_id",
    "run_with_escalation",
    "select_model",
    "true_task_cost",
]
