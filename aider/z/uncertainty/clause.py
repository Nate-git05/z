"""Typed task clauses (P1.1) — refinement of TaskIntent buckets.

Only ``requested_action`` and ``acceptance_criterion`` may become checklist
items. Observations, constraints, process rules, and background never do.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Literal, Optional, Sequence, Tuple

ClauseKind = Literal[
    "requested_action",
    "acceptance_criterion",
    "observation",
    "constraint",
    "process_rule",
    "investigation_target",
    "external_assumption",
    "background",
]
Polarity = Literal["required", "prohibited", "neutral"]

CHECKLIST_KINDS = frozenset({"requested_action", "acceptance_criterion"})


@dataclass
class TaskClause:
    text: str
    kind: ClauseKind
    polarity: Polarity = "neutral"
    confidence: float = 0.8
    source_span: Tuple[int, int] = (0, 0)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def checklist_eligible(self) -> bool:
        return self.kind in CHECKLIST_KINDS and self.polarity != "prohibited"


_PROCESS_RULE_RE = re.compile(
    r"(?i)\b("
    r"only\s+make\s+changes\s+after|"
    r"after\s+confirming\s+the\s+cause|"
    r"do\s+not\s+commit|don't\s+commit|"
    r"before\s+(?:finishing|committing|editing)|"
    r"until\s+(?:verified|the\s+cause|root\s+cause)|"
    r"confirm(?:ing)?\s+(?:the\s+)?(?:cause|root\s+cause)\s+before"
    r")\b"
)
_CONSTRAINT_RE = re.compile(
    r"(?i)\b("
    r"do\s+not|don't|without\s+(?:editing|changing|inventing)|"
    r"out\s+of\s+scope|must\s+not|never\s+"
    r")\b"
)
_OBSERVATION_RE = re.compile(
    r"(?i)\b("
    r"currently\s+(?:returns?|works?|fails?|errors?)|"
    r"returns?\s+an?\s+error|"
    r"was\s+reported|"
    r"the\s+(?:log|error|stack|output|report)\s+"
    r")\b"
)
_ENV_BACKGROUND_RE = re.compile(
    r"(?i)\b("
    r"reported\s+on\s+\w+|on\s+(?:linux|macos|windows|ubuntu)|"
    r"runs?\s+behind|in\s+production\s+we|"
    r"environment|os\s+version"
    r")\b"
)
_ASSUMPTION_RE = re.compile(
    r"(?i)\b(assumes?\s+that|given\s+that|we\s+assume|runs?\s+behind\s+a)\b"
)
_INVESTIGATE_RE = re.compile(
    r"(?i)\b(investigate|diagnose|determine\s+why|explain\s+why|"
    r"find\s+out|look\s+into|rule\s+out|confirm\s+the\s+cause)\b"
)
_ACCEPT_RE = re.compile(
    r"(?i)\b(done\s+when|acceptance|should\s+(?:pass|work)|must\s+pass|"
    r"verify\s+that)\b"
)
_ACTION_RE = re.compile(
    r"(?i)\b(implement|build|add|create|fix|change|update|refactor|"
    r"write|ship|introduce|make\s+(?:it|a|the))\b"
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S")


def _span_for(message: str, fragment: str) -> Tuple[int, int]:
    idx = (message or "").find(fragment)
    if idx < 0:
        return (0, max(0, len(fragment)))
    return (idx, idx + len(fragment))


def _split_multi_clause(sentence: str) -> List[str]:
    """Split one sentence that carries both constraint + action."""
    parts = re.split(r"(?i)\s*,\s*(?=only\b)|(?<=\w)\s+and\s+(?=only\b)", sentence)
    if len(parts) > 1:
        return [p.strip(" ,") for p in parts if p.strip(" ,")]
    # "Do not X, only Y" / "Do not X; only Y"
    m = re.match(
        r"(?i)^(?P<a>do\s+not\b.+?)\s*[,;]\s*(?P<b>only\b.+)$",
        sentence.strip(),
    )
    if m:
        return [m.group("a").strip(), m.group("b").strip()]
    return [sentence.strip()]


def classify_clause_text(text: str) -> Tuple[ClauseKind, Polarity, float]:
    """Classify one clause fragment. Prefer least-actionable on low confidence."""
    t = (text or "").strip()
    if not t:
        return "background", "neutral", 0.2

    # Constraints / prohibitions
    if re.search(r"(?i)\bthis\s+is\s+not\b", t) and not _ACTION_RE.search(t):
        return "constraint", "prohibited", 0.9
    if _CONSTRAINT_RE.search(t) and not (
        _ACTION_RE.search(t) and re.search(r"(?i)\bonly\s+fix\b|\bonly\s+make\b", t)
    ):
        # "do not invent…" is constraint; "only fix…" handled as action below
        if re.search(r"(?i)\bdo\s+not\b|\bdon't\b|\bmust\s+not\b|\bnever\b", t):
            return "constraint", "prohibited", 0.9
        if re.search(r"(?i)\bout\s+of\s+scope\b", t):
            return "constraint", "prohibited", 0.9

    if _PROCESS_RULE_RE.search(t):
        return "process_rule", "required", 0.9

    if _ACCEPT_RE.search(t):
        return "acceptance_criterion", "required", 0.85

    if _INVESTIGATE_RE.search(t) and not _ACTION_RE.search(t):
        return "investigation_target", "required", 0.85

    if _ASSUMPTION_RE.search(t) and not _ACTION_RE.search(t):
        return "external_assumption", "neutral", 0.8

    if _ENV_BACKGROUND_RE.search(t) and not _ACTION_RE.search(t):
        return "background", "neutral", 0.85

    if _OBSERVATION_RE.search(t) and not _ACTION_RE.search(t):
        return "observation", "neutral", 0.9

    if _ACTION_RE.search(t) or re.search(r"(?i)^\s*only\s+fix\b", t):
        return "requested_action", "required", 0.85

    # An explicit bullet/numbered list item that didn't match a more specific
    # category (constraint, process rule, observation, ...) is almost always
    # a requested action — the verb just isn't in _ACTION_RE's fixed list
    # (e.g. "email", "use", "notify", "deploy"). Treat it as one instead of
    # silently dropping it as background noise.
    if _BULLET_RE.match(t):
        return "requested_action", "required", 0.6

    # Low confidence — least actionable
    return "background", "neutral", 0.45


def extract_clauses(
    user_message: str,
    *,
    recent_messages: Sequence[str] = (),
) -> List[TaskClause]:
    """
    Decompose a message (+ recent context constraints) into TaskClause list.
    """
    text = (user_message or "").strip()
    clauses: List[TaskClause] = []
    seen: set[str] = set()

    def _emit(fragment: str, *, force: Optional[Tuple[ClauseKind, Polarity, float]] = None):
        frag = (fragment or "").strip()
        if len(frag) < 4:
            return
        key = frag.lower()
        if key in seen:
            return
        seen.add(key)
        if force:
            kind, polarity, conf = force
        else:
            kind, polarity, conf = classify_clause_text(frag)
        clauses.append(
            TaskClause(
                text=frag,
                kind=kind,
                polarity=polarity,
                confidence=conf,
                source_span=_span_for(text, frag),
            )
        )

    # Carry forward constraints from recent context
    for prev in recent_messages:
        for sent in re.split(r"(?<=[.!;])\s+|\n+", (prev or "").strip()):
            if _CONSTRAINT_RE.search(sent) or _PROCESS_RULE_RE.search(sent):
                kind, polarity, conf = classify_clause_text(sent)
                if kind in ("constraint", "process_rule"):
                    _emit(sent, force=(kind, polarity, conf))

    for sent in re.split(r"(?<=[.!;])\s+|\n+", text):
        sent = sent.strip()
        if not sent:
            continue
        for frag in _split_multi_clause(sent):
            _emit(frag)

    return clauses


def checklist_items_from_clauses(clauses: Sequence[TaskClause]) -> List[TaskClause]:
    """Only checklist-eligible clauses (P1.1 acceptance)."""
    return [c for c in clauses if c.checklist_eligible]


def constraints_from_clauses(clauses: Sequence[TaskClause]) -> List[TaskClause]:
    return [c for c in clauses if c.kind == "constraint"]


def process_rules_from_clauses(clauses: Sequence[TaskClause]) -> List[TaskClause]:
    return [c for c in clauses if c.kind == "process_rule"]


def clause_violates_constraints(step_text: str, clauses: Sequence[TaskClause]) -> Optional[TaskClause]:
    """Return the first constraint violated by a proposed plan step, if any."""
    step = (step_text or "").lower()
    for c in constraints_from_clauses(clauses):
        body = c.text.lower()
        # Extract topic after "do not" / "don't"
        m = re.search(
            r"(?i)(?:do\s+not|don't|must\s+not|never)\s+(?:invent\s+)?(?P<topic>.+)",
            body,
        )
        topic = (m.group("topic") if m else body).strip(" .")
        # Key nouns from constraint
        tokens = [
            t
            for t in re.split(r"[^\w]+", topic)
            if len(t) > 3 and t not in {"that", "this", "with", "from", "into"}
        ]
        if tokens and all(tok in step for tok in tokens[:2]):
            return c
        # Direct phrases
        for phrase in ("new mapping", "invent", "authentication", "auth module"):
            if phrase in body and phrase in step:
                return c
    return None


def process_rule_violated(
    clauses: Sequence[TaskClause],
    *,
    execution_log: str = "",
    edits_made: bool = False,
) -> Optional[TaskClause]:
    """
    Detect process_rule violations from session evidence.

    Example: "Only make changes after confirming the cause" + edits without
    a prior confirmation/root-cause note in the execution log.
    """
    log = (execution_log or "").lower()
    confirmed = bool(
        re.search(
            r"(?i)(root\s*cause|confirmed\s+cause|cause\s+confirmed|"
            r"diagnosed|reproduced|finding:)",
            log,
        )
    )
    for c in process_rules_from_clauses(clauses):
        body = c.text.lower()
        if re.search(r"(?i)after\s+confirming|confirm(?:ing)?\s+the\s+cause|before\s+edit", body):
            if edits_made and not confirmed:
                return c
        if re.search(r"(?i)do\s+not\s+commit|before\s+committing", body):
            if "commit" in log and not re.search(r"(?i)verify|tests?\s+pass", log):
                return c
    return None
