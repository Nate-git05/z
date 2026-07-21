"""Map TaskMode / intent text → CapabilityTier for gateway routing (Phase 5).

Kept free of uncertainty/repo imports so ``z_server`` can call it safely.
"""

from __future__ import annotations

import re
from typing import Optional, Union

from .registry import TIER_ORDER, CapabilityTier

# TaskMode value → minimum capability tier for the first attempt.
_TASK_MODE_TIER = {
    "ask": CapabilityTier.TRIVIAL,
    "investigate": CapabilityTier.MODERATE,
    "review": CapabilityTier.MODERATE,
    "verify": CapabilityTier.MODERATE,
    "plan": CapabilityTier.HARD,
    "implement": CapabilityTier.MODERATE,
}

_HARD_INTENT_RE = re.compile(
    r"(?i)\b("
    r"race|concurren|deadlock|migrat|security|auth(?:n|z|entication)?|"
    r"architect|refactor\s+all|multi[- ]file|distributed|crypto"
    r")\b"
)
_REASONING_INTENT_RE = re.compile(
    r"(?i)\b("
    r"design\s+(?:the\s+)?system|ambiguous|trade[- ]?offs?|"
    r"from\s+scratch|greenfield|architecture\s+review"
    r")\b"
)
_TRIVIAL_INTENT_RE = re.compile(
    r"(?i)\b("
    r"rename|typo|format|whitespace|comment\s+only|simple\s+docstring"
    r")\b"
)


def normalize_task_mode(task_mode: Optional[Union[str, object]]) -> Optional[str]:
    if task_mode is None:
        return None
    if hasattr(task_mode, "value"):
        task_mode = getattr(task_mode, "value")
    text = str(task_mode).strip().lower()
    return text or None


def tier_for_task_mode(task_mode: Optional[Union[str, object]]) -> CapabilityTier:
    """Default tier floor for a TaskMode (before escalation)."""
    key = normalize_task_mode(task_mode)
    if not key:
        return CapabilityTier.MODERATE
    return _TASK_MODE_TIER.get(key, CapabilityTier.MODERATE)


def tier_from_intent_text(text: Optional[str]) -> CapabilityTier:
    """Lightweight text heuristics when TaskMode is absent."""
    blob = (text or "").strip()
    if not blob:
        return CapabilityTier.MODERATE
    if _REASONING_INTENT_RE.search(blob):
        return CapabilityTier.REASONING_HEAVY
    if _HARD_INTENT_RE.search(blob):
        return CapabilityTier.HARD
    if _TRIVIAL_INTENT_RE.search(blob) and len(blob) < 120:
        return CapabilityTier.TRIVIAL
    return CapabilityTier.MODERATE


def resolve_capability_tier(
    *,
    task_mode: Optional[Union[str, object]] = None,
    intent: Optional[str] = None,
    tier_hint: Optional[str] = None,
) -> CapabilityTier:
    """Resolve tier: explicit hint > TaskMode > intent text > MODERATE."""
    if tier_hint:
        raw = str(tier_hint).strip().lower()
        for t in TIER_ORDER:
            if t.value == raw:
                return t
    mode = normalize_task_mode(task_mode)
    if mode:
        base = tier_for_task_mode(mode)
        # Intent can only raise the floor for implement/plan, not lower ask.
        if intent and mode in ("implement", "plan", "investigate"):
            intent_tier = tier_from_intent_text(intent)
            if TIER_ORDER.index(intent_tier) > TIER_ORDER.index(base):
                return intent_tier
        return base
    if intent:
        return tier_from_intent_text(intent)
    return CapabilityTier.MODERATE


def bump_tier(tier: CapabilityTier, depth: int) -> CapabilityTier:
    """Escalate *depth* steps up the ladder (clamped)."""
    if depth <= 0:
        return tier
    idx = TIER_ORDER.index(tier)
    return TIER_ORDER[min(idx + int(depth), len(TIER_ORDER) - 1)]


# OpenAI-compatible upstream fallbacks when the selected provider has no key.
OPENAI_TIER_FALLBACK = {
    CapabilityTier.TRIVIAL: "gpt-4o-mini",
    CapabilityTier.MODERATE: "gpt-4o-mini",
    CapabilityTier.HARD: "gpt-4o",
    CapabilityTier.REASONING_HEAVY: "o3-mini",
}
