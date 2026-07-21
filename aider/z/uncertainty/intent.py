"""Structured task intent — single upstream classification for planning/caps.

P1.1 Path A: ``clauses`` is authoritative; classic bucket fields are synced
from clauses after extraction so P0 consumers keep working.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence

from .clause import CHECKLIST_KINDS, TaskClause, extract_clauses

logger = logging.getLogger(__name__)


@dataclass
class TaskIntent:
    """What the user asked for — separated from background and exclusions."""

    mode: str = "implement"  # ask|investigate|review|verify|implement
    clauses: List[TaskClause] = field(default_factory=list)
    requested_actions: List[str] = field(default_factory=list)
    prohibited_actions: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "clauses": [c.to_dict() for c in self.clauses],
            "requested_actions": list(self.requested_actions),
            "prohibited_actions": list(self.prohibited_actions),
            "observations": list(self.observations),
            "acceptance_criteria": list(self.acceptance_criteria),
        }

    @property
    def planning_text(self) -> str:
        parts = list(self.requested_actions) + list(self.acceptance_criteria)
        return "\n".join(p for p in parts if p and p.strip())

    @property
    def capability_text(self) -> str:
        return "\n".join(p for p in self.requested_actions if p and p.strip())

    def sync_buckets_from_clauses(self) -> None:
        """Derive classic buckets from typed clauses (Path A)."""
        if not self.clauses:
            return
        self.requested_actions = [
            c.text
            for c in self.clauses
            if c.kind == "requested_action" and c.polarity != "prohibited"
        ]
        self.prohibited_actions = [
            c.text for c in self.clauses if c.kind == "constraint"
        ]
        self.observations = [
            c.text
            for c in self.clauses
            if c.kind in ("observation", "background", "external_assumption")
        ]
        self.acceptance_criteria = [
            c.text for c in self.clauses if c.kind == "acceptance_criterion"
        ]


_INVESTIGATE_VERBS = re.compile(
    r"(?i)\b(investigate|diagnose|determine|explain|find out|figure out|"
    r"why does|why is|what causes|look into|trace)\b"
)


def extract_intent(
    user_message: str,
    *,
    recent_messages: Sequence[str] = (),
    forced_mode: Optional[str] = None,
) -> TaskIntent:
    """
    Classify a user turn into TaskIntent with TaskClause detail (P0.2 + P1.1).
    """
    text = (user_message or "").strip()
    clauses = extract_clauses(text, recent_messages=recent_messages)

    mode = forced_mode
    if not mode:
        from aider.z.task_mode import classify_task_mode

        tm = classify_task_mode(None, text)
        has_invest = any(c.kind == "investigation_target" for c in clauses) or bool(
            _INVESTIGATE_VERBS.search(text)
        )
        has_action = any(c.kind == "requested_action" for c in clauses)
        has_constraint = any(c.kind == "constraint" for c in clauses)
        if has_invest and not has_action:
            mode = "investigate"
        elif has_constraint and not has_action and not has_invest:
            mode = "investigate"
        elif re.search(r"(?i)\bonly reproduce\b|\breproduce locally\b", text):
            mode = "investigate"
        else:
            mode = tm.value

    resolved_mode = mode or "implement"

    # Never fabricate a coding action from a greeting / small-talk / topic turn.
    # Respect forced_mode (e.g. one-shot /code <topic>) — D8.
    try:
        from aider.z.task_mode import (
            looks_like_ambiguous_topic,
            looks_like_ask_question,
            looks_like_casual_chat,
        )

        if not forced_mode and (
            looks_like_casual_chat(text)
            or looks_like_ambiguous_topic(text)
            or looks_like_ask_question(text)
        ):
            resolved_mode = "ask"
            intent = TaskIntent(mode="ask", clauses=list(clauses))
            intent.sync_buckets_from_clauses()
            return intent
    except Exception:
        pass

    actionable = [c for c in clauses if c.kind in CHECKLIST_KINDS]
    if resolved_mode == "implement" and not actionable and text.strip():
        non_action = {c.kind for c in clauses}
        if non_action and non_action <= {
            "observation",
            "background",
            "constraint",
            "process_rule",
            "external_assumption",
            "investigation_target",
        }:
            if "investigation_target" in non_action or "constraint" in non_action:
                resolved_mode = "investigate"
            elif re.search(r"(?i)\bonly reproduce\b|\breproduce locally\b", text):
                resolved_mode = "investigate"
            else:
                clauses.append(
                    TaskClause(
                        text=text.strip(),
                        kind="requested_action",
                        polarity="required",
                        confidence=0.55,
                        source_span=(0, len(text)),
                    )
                )
        elif not clauses:
            # Bare text with no clauses: only invent an action when it looks
            # like a real coding request, not chat or an ambiguous topic.
            from aider.z.task_mode import (
                has_implement_signal,
                looks_like_ambiguous_topic,
            )

            if looks_like_ambiguous_topic(text):
                resolved_mode = "ask"
            elif has_implement_signal(text) or len(text) > 40:
                clauses.append(
                    TaskClause(
                        text=text.strip(),
                        kind="requested_action",
                        polarity="required",
                        confidence=0.55,
                        source_span=(0, len(text)),
                    )
                )
            else:
                resolved_mode = "ask"

    intent = TaskIntent(mode=resolved_mode, clauses=list(clauses))
    intent.sync_buckets_from_clauses()
    logger.info(
        "TaskIntent mode=%s clauses=%d actions=%d constraints=%d",
        intent.mode,
        len(intent.clauses),
        len(intent.requested_actions),
        len(intent.prohibited_actions),
    )
    return intent


def intent_mentions_prohibited(intent: TaskIntent, topic: str) -> bool:
    """True if ``topic`` appears in an explicit prohibition/constraint."""
    t = topic.lower().strip()
    if not t:
        return False
    for p in intent.prohibited_actions:
        if t in p.lower():
            return True
    return False
