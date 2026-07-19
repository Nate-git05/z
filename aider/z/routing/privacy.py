"""Customer-controlled provider endpoints — compliance allowlist boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ProviderEndpoint:
    """One customer-registered model vendor endpoint.

    Allowlist membership is what gates selection — compliance_tags are
    informational only. Secrets live behind auth_ref, never in this record.
    """

    provider: str
    base_url: str
    auth_ref: str  # reference to a secret, never the secret itself
    self_hosted: bool = False
    compliance_tags: Tuple[str, ...] = ()
