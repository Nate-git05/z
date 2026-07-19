"""Gate-triggered escalation ladder — cost-honest attempt accounting.

Reuses the bounded reflect-loop shape from auto_act (max_attempts) and the
existing prepare_commit gate. run_model / prepare_commit are injectable so
unit tests don't need a full coder session.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple, TYPE_CHECKING

from .classify import classify_task
from .registry import TIER_ORDER, CapabilityTier, ModelProfile
from .select import select_model

if TYPE_CHECKING:
    from aider.z.uncertainty.gate import GateResult

    from .calibration import CalibrationStore
    from .config import RoutingPolicy
    from .registry import PricingCache


@dataclass
class RoutingAttempt:
    model_id: str
    tier: CapabilityTier
    cost_usd: float
    gate_passed: bool
    escalated_to: Optional[str] = None


@dataclass
class RoutingTask:
    """Minimal task handle for the escalation loop."""

    root: Path
    request_text: str
    target_files: Sequence[str]
    context_tokens: int = 4096
    latency_budget_ms: Optional[int] = None
    edited_files: Sequence[str] = ()
    coder: Any = None


def true_task_cost(attempts: Sequence[RoutingAttempt]) -> float:
    """Sum of every attempt's cost — not just the final model."""
    return sum(a.cost_usd for a in attempts)


def run_with_escalation(
    task: RoutingTask,
    policy: "RoutingPolicy",
    pricing: "PricingCache",
    calibration: "CalibrationStore",
    *,
    run_model: Optional[
        Callable[[ModelProfile, RoutingTask], Tuple[str, float]]
    ] = None,
    prepare_commit_fn: Optional[Callable[..., "GateResult"]] = None,
) -> Tuple[List[RoutingAttempt], "GateResult"]:
    """
    Classify → select → run → gate; escalate tier on failure until budget.

    When prepare_commit_fn / run_model are omitted, uses production wiring
    (prepare_commit from gate.py). Tests inject fakes.
    """
    from aider.z.uncertainty.gate import GateResult
    from aider.z.uncertainty.risk import collect_base_signals

    if run_model is None:
        raise RuntimeError(
            "run_model callback required — production wiring supplies the "
            "ephemeral proxy call; unit tests inject a fake."
        )
    if prepare_commit_fn is None:
        from aider.z.uncertainty.gate import prepare_commit as prepare_commit_fn

    attempts: List[RoutingAttempt] = []
    tier = classify_task(task.root, task.request_text, task.target_files)
    gate_result: GateResult = GateResult(allow_commit=False, reason="no attempts")
    spent = 0.0

    for depth in range(policy.max_escalations + 1):
        # Cost ceiling: never start an escalation step that would likely breach.
        # First attempt (depth 0) always runs; later steps use the prior attempt
        # cost as a proxy for the next call's spend.
        if depth > 0 and policy.cost_ceiling_per_task_usd is not None:
            prior = attempts[-1].cost_usd if attempts else 0.0
            if spent + prior > policy.cost_ceiling_per_task_usd:
                gate_result = GateResult(
                    allow_commit=False,
                    reason=(
                        f"next escalation would exceed cost ceiling "
                        f"${policy.cost_ceiling_per_task_usd:.4f}; "
                        f"{policy.cost_ceiling_action}"
                    ),
                )
                break

        model = select_model(
            tier,
            policy=policy,
            context_tokens=task.context_tokens,
            latency_budget_ms=task.latency_budget_ms,
            pricing=pricing,
            calibration=calibration,
        )

        _diff, cost = run_model(model, task)
        spent += cost

        edited = list(task.edited_files or task.target_files or ())
        signals = collect_base_signals(edited)
        if (
            signals.concurrency_relevant or signals.high_stakes_hit
        ) and tier != CapabilityTier.REASONING_HEAVY:
            tier = CapabilityTier.REASONING_HEAVY

        gate_result = prepare_commit_fn(task.coder, edited)
        attempts.append(
            RoutingAttempt(
                model_id=model.model_id,
                tier=tier,
                cost_usd=float(cost),
                gate_passed=bool(gate_result.allow_commit),
            )
        )
        calibration.record_outcome(
            model.model_id,
            tier.value,
            bool(gate_result.allow_commit),
            escalated=depth > 0,
            cost_usd=float(cost),
            customer_id=policy.customer_id,
        )

        if gate_result.allow_commit:
            return attempts, gate_result

        # Escalate tier for next attempt
        idx = TIER_ORDER.index(tier)
        next_tier = TIER_ORDER[min(idx + 1, len(TIER_ORDER) - 1)]
        if attempts:
            attempts[-1].escalated_to = next_tier.value
        tier = next_tier

    return attempts, gate_result
