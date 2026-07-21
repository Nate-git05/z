"""Structured task intent — single upstream classification for planning/caps.

Downstream systems (planner, capability inference) must consume ``TaskIntent``
fields only. They must not re-scan the raw user prompt for domain keywords.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class TaskIntent:
    """What the user asked for — separated from background and exclusions."""

    mode: str = "implement"  # ask|investigate|review|verify|implement
    requested_actions: List[str] = field(default_factory=list)
    prohibited_actions: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def planning_text(self) -> str:
        """Only fields allowed to drive plan steps / template selection."""
        parts = list(self.requested_actions) + list(self.acceptance_criteria)
        return "\n".join(p for p in parts if p and p.strip())

    @property
    def capability_text(self) -> str:
        """Only required requested actions may activate capabilities."""
        return "\n".join(p for p in self.requested_actions if p and p.strip())


_PROHIBIT_RE = re.compile(
    r"(?i)\b(?:"
    r"do not|don't|do\s+not|"
    r"without(?:\s+\w+){0,3}\s+(?:editing|changing|modifying|touching)|"
    r"this is not (?:a |an )?"
    r")\s*(?P<body>[^.;\n]+)"
)
_OUT_OF_SCOPE_RE = re.compile(
    r"(?i)\b(?P<body>[\w][\w\s/-]{0,40}?)\s+(?:is|are)\s+(?:explicitly\s+)?out of scope\b"
)
_NOT_AN_X_RE = re.compile(
    r"(?i)\bthis is not (?:a |an )?(?P<topic>[\w\s/-]+?)(?:\s+issue|\s+problem|;|,|\.|$)"
)
_OBSERVE_RE = re.compile(
    r"(?i)^(?:"
    r"the (?:report|log|error|stack(?:\s*trace)?|output)|"
    r"(?:it|this|the api|the request) (?:fails|failed|errors|is broken)|"
    r"error:|traceback|exception"
    r")"
)
_INVESTIGATE_VERBS = re.compile(
    r"(?i)\b(investigate|diagnose|determine|explain|find out|figure out|"
    r"why does|why is|what causes|look into|trace)\b"
)
_IMPLEMENT_VERBS = re.compile(
    r"(?i)\b(implement|build|add|create|fix|change|update|refactor|write|"
    r"make|ship|introduce)\b"
)
_ACCEPT_RE = re.compile(
    r"(?i)\b(?:done when|acceptance|should (?:pass|work|return)|"
    r"must (?:pass|work)|verify that|so that)\b\s*(?P<body>.+)"
)


def _split_sentences(text: str) -> List[str]:
    chunks = re.split(r"(?<=[.!;])\s+|\n+", text.strip())
    return [c.strip() for c in chunks if c and c.strip()]


def extract_intent(
    user_message: str,
    *,
    recent_messages: Sequence[str] = (),
    forced_mode: Optional[str] = None,
) -> TaskIntent:
    """
    Classify a user turn into TaskIntent.

    Deterministic heuristic extractor (CI-safe). Optional LLM extraction can
    wrap this later; tests assert against this function's contract.
    """
    text = (user_message or "").strip()
    context = "\n".join(m for m in recent_messages if m).strip()
    blob = f"{context}\n{text}".strip() if context else text

    prohibited: List[str] = []
    observations: List[str] = []
    requested: List[str] = []
    acceptance: List[str] = []

    # Standing exclusions from recent context + current turn
    for m in _PROHIBIT_RE.finditer(blob):
        body = (m.group("body") or "").strip(" .")
        if body and body.lower() not in {p.lower() for p in prohibited}:
            prohibited.append(body)
    for m in _OUT_OF_SCOPE_RE.finditer(blob):
        body = (m.group("body") or "").strip(" .")
        if body and body.lower() not in {p.lower() for p in prohibited}:
            prohibited.append(f"{body} out of scope")
    for m in _NOT_AN_X_RE.finditer(blob):
        topic = (m.group("topic") or "").strip()
        if topic:
            phrase = f"not a {topic} issue"
            if phrase.lower() not in {p.lower() for p in prohibited}:
                prohibited.append(phrase)

    for sent in _split_sentences(text):
        lower = sent.lower()
        # Acceptance criteria
        am = _ACCEPT_RE.search(sent)
        if am:
            body = (am.group("body") or "").strip()
            if body:
                acceptance.append(body)

        # Explicit prohibitions already captured; skip converting them to actions
        if re.search(r"(?i)\b(do not|don't|out of scope|this is not)\b", lower):
            # Still may hold an observation half ("…; investigate the API")
            if _INVESTIGATE_VERBS.search(sent) or _IMPLEMENT_VERBS.search(sent):
                # Keep the non-prohibited clause if semicolon-split
                parts = re.split(r"[;]", sent)
                for part in parts:
                    pl = part.lower().strip()
                    if re.search(r"(?i)\b(do not|don't|out of scope|this is not)\b", pl):
                        continue
                    if _INVESTIGATE_VERBS.search(part) or _IMPLEMENT_VERBS.search(part):
                        if part.strip() and part.strip() not in requested:
                            requested.append(part.strip())
            continue

        if _OBSERVE_RE.search(sent) and not _IMPLEMENT_VERBS.search(sent):
            observations.append(sent)
            continue

        if _INVESTIGATE_VERBS.search(sent) or _IMPLEMENT_VERBS.search(sent):
            requested.append(sent)
            continue

        # Background / descriptive sentences
        if sent:
            observations.append(sent)

    # Mode resolution
    mode = forced_mode
    if not mode:
        from aider.z.task_mode import classify_task_mode

        # Use heuristic mode from message; ask/context forced_mode set by caller
        tm = classify_task_mode(None, text)
        # If only investigate verbs + prohibitions, force investigate
        if _INVESTIGATE_VERBS.search(text) and not (
            _IMPLEMENT_VERBS.search(text)
            and not re.search(r"(?i)\b(do not|don't)\b", text)
        ):
            if re.search(
                r"(?i)\b(do not|don't)\s+(?:edit|change|modify|touch|add|create)",
                text,
            ) or (
                _INVESTIGATE_VERBS.search(text)
                and not re.search(r"(?i)\b(fix|implement|add|create|build)\b", text)
            ):
                mode = "investigate"
            else:
                mode = tm.value
        else:
            mode = tm.value

    resolved_mode = mode or "implement"
    # Implement-mode with no parsed actions: treat the whole turn as the request
    # unless the message is primarily exclusions / "do not X" with no edit ask.
    if resolved_mode == "implement" and not requested and text.strip():
        pure_exclusion = bool(prohibited) and not _IMPLEMENT_VERBS.search(
            re.sub(
                r"(?i)\b(do not|don't|out of scope|this is not)\b[^.;\n]*",
                " ",
                text,
            )
        )
        if pure_exclusion:
            resolved_mode = "investigate"
        else:
            # Soften "mentions production / only reproduce locally" style reports
            if re.search(r"(?i)\bonly reproduce\b|\breproduce locally\b", text):
                resolved_mode = "investigate"
                observations.append(text.strip())
            else:
                requested = [text.strip()]
                observations = [o for o in observations if o.strip() != text.strip()]

    intent = TaskIntent(
        mode=resolved_mode,
        requested_actions=requested,
        prohibited_actions=prohibited,
        observations=observations,
        acceptance_criteria=acceptance,
    )
    logger.info(
        "TaskIntent mode=%s actions=%d prohibited=%d observations=%d",
        intent.mode,
        len(intent.requested_actions),
        len(intent.prohibited_actions),
        len(intent.observations),
    )
    return intent


def intent_mentions_prohibited(intent: TaskIntent, topic: str) -> bool:
    """True if ``topic`` appears in an explicit prohibition."""
    t = topic.lower().strip()
    if not t:
        return False
    for p in intent.prohibited_actions:
        if t in p.lower():
            return True
    return False
