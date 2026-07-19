"""Per-customer routing policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from .privacy import ProviderEndpoint


@dataclass
class RoutingPolicy:
    customer_id: str
    allowed_endpoints: Tuple[ProviderEndpoint, ...] = ()
    max_escalations: int = 2
    cost_ceiling_per_task_usd: Optional[float] = None
    # never "silently exceed"
    cost_ceiling_action: str = "surface_to_human"
    # e.g. "groq-llama-70b" for autocomplete — skips classify_task
    fast_lane_provider: Optional[str] = None

    def allowed_providers(self) -> set[str]:
        return {e.provider for e in self.allowed_endpoints}
