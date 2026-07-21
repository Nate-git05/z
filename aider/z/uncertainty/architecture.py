"""Architecture risk review — boundaries before files.

Before coding multi-component features, Z answers an architecture checklist:
shared state, runtime model, trust boundaries, concurrency, persistence,
public/private contracts, and failure recovery. Unknown critical items become
blocking assumptions rather than silent guesses.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence


@dataclass
class ArchitectureItem:
    """One architecture checkpoint question."""

    id: str
    prompt: str
    status: str = "unknown"  # known | unknown | blocked
    answer: str = ""
    critical: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ArchitectureCheckpoint:
    """Pre-implementation architecture review."""

    items: List[ArchitectureItem] = field(default_factory=list)
    recommended_layers: List[str] = field(default_factory=list)
    blocking_assumptions: List[str] = field(default_factory=list)

    @property
    def all_critical_known(self) -> bool:
        return all(
            i.status == "known" for i in self.items if i.critical
        )

    def to_dict(self) -> dict:
        return {
            "items": [i.to_dict() for i in self.items],
            "recommended_layers": list(self.recommended_layers),
            "blocking_assumptions": list(self.blocking_assumptions),
            "all_critical_known": self.all_critical_known,
        }


_CHECKLIST_PROMPTS: Sequence[tuple[str, str, bool]] = (
    ("shared_state", "Shared state requirements understood", True),
    ("runtime_model", "Deployment / runtime model understood", True),
    ("trust_boundaries", "Trust boundaries identified", True),
    ("concurrency", "Concurrency model considered", True),
    ("persistence", "Persistence expectations defined", True),
    ("contracts", "Public / private data contracts defined", True),
    ("failure_recovery", "Failure recovery defined", True),
)

_LAYER_STACK = [
    "UI",
    "typed API client",
    "authenticated route/controller",
    "domain/service layer (rules)",
    "repository / state adapter",
    "deterministic clock & ID interfaces (for tests)",
]

_TRIGGER = re.compile(
    r"(?i)\b("
    r"multiplayer|multi-?user|lobby|game|match|realtime|websocket|"
    r"shared\s+state|session|collaborat|api|backend|server|auth|"
    r"database|persist|concurren|race"
    r")\b"
)


def architecture_review_needed(requirements: str) -> bool:
    return bool(_TRIGGER.search(requirements or ""))


def draft_architecture_checkpoint(
    requirements: str,
    *,
    known_answers: Optional[dict] = None,
) -> ArchitectureCheckpoint:
    """
    Produce an architecture checklist. Items stay ``unknown`` until answered;
    critical unknowns become blocking assumptions.
    """
    known_answers = known_answers or {}
    text = requirements or ""
    items: List[ArchitectureItem] = []
    blocking: List[str] = []

    heuristics = _heuristic_answers(text)

    for item_id, prompt, critical in _CHECKLIST_PROMPTS:
        answer = (known_answers.get(item_id) or heuristics.get(item_id) or "").strip()
        status = "known" if answer else "unknown"
        item = ArchitectureItem(
            id=item_id,
            prompt=prompt,
            status=status,
            answer=answer,
            critical=critical,
        )
        items.append(item)
        if critical and status == "unknown":
            blocking.append(
                f"[ ] {prompt} — investigate or record an explicit assumption "
                "before coding."
            )

    layers = list(_LAYER_STACK) if architecture_review_needed(text) else []
    return ArchitectureCheckpoint(
        items=items,
        recommended_layers=layers,
        blocking_assumptions=blocking,
    )


def _heuristic_answers(text: str) -> dict:
    """Lightweight defaults from request wording — still marked known only
    when the text actually supports them."""
    out: dict = {}
    if re.search(r"(?i)\b(shared|lobby|multiplayer|server\s+state|redis|db)\b", text):
        out["shared_state"] = (
            "Shared across clients via server-side store (not client-only memory)."
        )
    if re.search(r"(?i)\b(next\.?js|vercel|cloud\s*run|docker|node\s+server)\b", text):
        out["runtime_model"] = (
            "Web app with server handlers; assume multi-instance unless stated."
        )
    if re.search(r"(?i)\b(auth|login|session|jwt|cookie)\b", text):
        out["trust_boundaries"] = (
            "Client identity is untrusted; authorize on the server; validate input."
        )
    if re.search(r"(?i)\b(concurren|race|simultaneous|parallel|two\s+players)\b", text):
        out["concurrency"] = (
            "Concurrent requests expected; design for duplicate/idempotent actions."
        )
    if re.search(r"(?i)\b(persist|database|redis|restart|durable)\b", text):
        out["persistence"] = (
            "State must survive process restart via durable store."
        )
    elif re.search(r"(?i)\b(in-?memory|ephemeral|demo)\b", text):
        out["persistence"] = (
            "Ephemeral in-memory state acceptable; document restart loss."
        )
    if re.search(r"(?i)\b(api|endpoint|schema|contract|typescript|zod)\b", text):
        out["contracts"] = (
            "Explicit request/response schemas; separate domain state from public DTOs."
        )
    if re.search(r"(?i)\b(timeout|expir|reconnect|leave|disconnect|error)\b", text):
        out["failure_recovery"] = (
            "Define timeout, disconnect, and error recovery paths for each state."
        )
    return out


def format_architecture_checkpoint(cp: ArchitectureCheckpoint) -> str:
    lines = [
        "Architecture checkpoint (answer before coding):",
        "",
    ]
    for item in cp.items:
        mark = "[x]" if item.status == "known" else "[ ]"
        lines.append(f"  {mark} {item.prompt}")
        if item.answer:
            lines.append(f"      → {item.answer}")
    if cp.recommended_layers:
        lines.append("")
        lines.append("Recommended boundaries:")
        lines.append("  " + " → ".join(cp.recommended_layers))
    if cp.blocking_assumptions:
        lines.append("")
        lines.append("Blocking unknowns:")
        for b in cp.blocking_assumptions:
            lines.append(f"  {b}")
    return "\n".join(lines)
