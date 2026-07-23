"""First-class task modes that gate the agent control-flow pipeline.

``edit_format`` remains a response-rendering setting. ``TaskMode`` answers:
should implementation machinery (planning, capability inference, edits) run
for *this* message?
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class TaskMode(Enum):
    ASK = "ask"
    INVESTIGATE = "investigate"
    IMPLEMENT = "implement"
    REVIEW = "review"
    VERIFY = "verify"
    PLAN = "plan"  # OpenCode-style: design only; no product edits

    # --- pipeline policy (single source of truth) ---------------------------

    @property
    def allows_planning(self) -> bool:
        # PLAN drafts a plan; IMPLEMENT may still gate high-stakes plans
        return self in (TaskMode.IMPLEMENT, TaskMode.PLAN)

    @property
    def allows_requirement_decomposition(self) -> bool:
        # INVESTIGATE may build investigation targets; REVIEW limited clauses;
        # IMPLEMENT/PLAN full checklist. ASK/VERIFY skip.
        return self in (
            TaskMode.IMPLEMENT,
            TaskMode.INVESTIGATE,
            TaskMode.REVIEW,
            TaskMode.PLAN,
        )

    @property
    def allows_capability_inference(self) -> bool:
        return self is TaskMode.IMPLEMENT

    @property
    def allows_edits(self) -> bool:
        """Product-code edits. PLAN uses ``allows_plan_file_edits`` instead."""
        return self is TaskMode.IMPLEMENT

    @property
    def allows_plan_file_edits(self) -> bool:
        """Only the plan artifact may be written in PLAN mode."""
        return self is TaskMode.PLAN

    @property
    def skills_read_only(self) -> bool:
        return self is not TaskMode.IMPLEMENT

    @property
    def allows_shell_readonly(self) -> bool:
        return True  # all modes may run read-only shell to answer

    @property
    def allows_shell_verification(self) -> bool:
        return self in (TaskMode.IMPLEMENT, TaskMode.VERIFY, TaskMode.REVIEW)

    @property
    def allows_shell_mutation(self) -> bool:
        return self is TaskMode.IMPLEMENT

    @property
    def allows_explore_pass(self) -> bool:
        return self in (TaskMode.IMPLEMENT, TaskMode.PLAN, TaskMode.INVESTIGATE)


_INVESTIGATE_RE = re.compile(
    r"(?i)\b("
    r"investigate|diagnose|determine why|figure out why|explain why|"
    r"why does|why is|what causes|root cause|do not edit|don't edit|"
    r"without (?:editing|changing|modifying)|no edits?|read[- ]only|"
    r"just (?:look|inspect|check|explain)|find (?:which|what|where)"
    r")\b"
)
_REVIEW_RE = re.compile(
    r"(?i)\b(review (?:this|the|my)|look (?:over|at) (?:this|the) (?:diff|pr|change)|"
    r"code review|please review)\b"
)
_VERIFY_RE = re.compile(
    r"(?i)\b(run (?:the )?tests?|verify (?:that|the)|check (?:if|whether)|"
    r"typecheck|lint (?:the|this)|does (?:it|this) pass)\b"
)
_IMPLEMENT_RE = re.compile(
    r"(?i)\b("
    r"implement|build|add|create|fix|change|update|refactor|write|"
    r"make (?:it|a|the)|please (?:add|fix|implement|build)|ship"
    r")\b"
)

# Casual chat / small-talk — must NOT open the implementation plan UX.
_CASUAL_CHAT_RE = re.compile(
    r"(?i)^\s*("
    r"hi|hello|hey|yo|sup|howdy|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"how\s+are\s+you(?:\s+doing)?|what'?s\s+up|whats\s+up|"
    r"thanks|thank\s+you|thx|ty|"
    r"ok|okay|cool|nice|great|awesome|got\s+it|sounds?\s+good|"
    r"bye|goodbye|see\s+ya|later"
    r")(?:\s*[!.?]*)?\s*$"
)
_PURE_QUESTION_RE = re.compile(
    r"(?i)^\s*(what|who|when|where|why|how|is|are|can|could|should|would|do|does|did)\b"
)

# Topic-ish tokens: letters/digits with optional inner hyphens/underscores/apostrophes
_TOPIC_TOKEN_RE = re.compile(r"(?i)^[a-z0-9][a-z0-9_'/-]*$")
_TOPIC_JOINERS = frozenset({"and", "or", "the", "a", "an", "of", "for", "to", "in", "on", "with"})


def _mode_classify_enabled() -> bool:
    """Escape: Z_MODE_CLASSIFY=0 restores pre-A1 ambiguous→IMPLEMENT default."""
    import os

    raw = (os.environ.get("Z_MODE_CLASSIFY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def has_implement_signal(user_message: str) -> bool:
    """True when the message includes an implement/build/fix-style verb."""
    return bool(_IMPLEMENT_RE.search(user_message or ""))


_CLASSIFY_SYSTEM_PROMPT = (
    "You are a fast intent classifier for a coding assistant chat turn. "
    "Read the user's message and output EXACTLY ONE WORD, lowercase, "
    "nothing else — no punctuation, no explanation:\n"
    "  ask         - answer a question or discuss; no code changes needed\n"
    "  investigate - explain/diagnose/find something; read-only, no edits\n"
    "  review      - review existing code/diff/PR; no new edits requested\n"
    "  verify      - run tests/checks to confirm something works\n"
    "  implement   - write/change/fix code; a concrete edit is requested\n"
    "Respond with only one of: ask, investigate, review, verify, implement."
)

_MODE_CLASSIFY_TOKENS = {
    "ask": TaskMode.ASK,
    "investigate": TaskMode.INVESTIGATE,
    "implement": TaskMode.IMPLEMENT,
    "review": TaskMode.REVIEW,
    "verify": TaskMode.VERIFY,
}


def _mode_classify_timeout() -> float:
    """Z_MODE_CLASSIFY_TIMEOUT seconds (default 3.0) — a hard cap independent
    of the model's own retry loop, since this must stay a cheap, fast gate,
    not a generation the user is waiting on."""
    import os

    raw = os.environ.get("Z_MODE_CLASSIFY_TIMEOUT", "3.0")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 3.0
    return val if val > 0 else 3.0


def _classify_via_weak_model(user_message: str, classifier_model) -> Optional["TaskMode"]:
    """One-shot weak-model escalation for the ambiguous fallback zone.

    Returns None on ANY failure/timeout/disabled/unparseable output — the
    caller keeps the existing regex default. Never raises.
    """
    if classifier_model is None:
        return None
    text = (user_message or "").strip()
    if not text:
        return None

    from aider.z.latency import join_future, submit_background

    def _call():
        messages = [
            {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
            {"role": "user", "content": text[:2000]},
        ]
        return classifier_model.simple_send_with_retries(messages)

    try:
        fut = submit_background(_call)
    except Exception:
        return None
    raw = join_future(fut, timeout=_mode_classify_timeout())
    if not raw or not isinstance(raw, str):
        return None

    for word in re.findall(r"[a-z]+", raw.strip().lower()):
        if word in _MODE_CLASSIFY_TOKENS:
            return _MODE_CLASSIFY_TOKENS[word]
    return None


def looks_like_casual_chat(user_message: str) -> bool:
    """True for greetings / small-talk that should stay in ASK (no plan gate)."""
    text = (user_message or "").strip()
    if not text:
        return False
    if _CASUAL_CHAT_RE.match(text):
        return True
    # Ultra-short filler without any implement/investigate signal
    if (
        len(text) <= 16
        and len(text.split()) <= 3
        and not _IMPLEMENT_RE.search(text)
        and not _INVESTIGATE_RE.search(text)
        and not _VERIFY_RE.search(text)
        and not _REVIEW_RE.search(text)
        and not re.search(r"[/\\.`]", text)
    ):
        return True
    return False


def looks_like_ask_question(user_message: str) -> bool:
    """Informational question with no implement/verify signal → ASK."""
    text = (user_message or "").strip()
    if not text or not _PURE_QUESTION_RE.match(text):
        return False
    if _IMPLEMENT_RE.search(text) or _VERIFY_RE.search(text) or _REVIEW_RE.search(text):
        return False
    # "why does X fail" investigate path handles separately
    if _INVESTIGATE_RE.search(text):
        return False
    return True


def looks_like_ambiguous_topic(user_message: str) -> bool:
    """True for short noun-ish topics with no verb — ASK, not IMPLEMENT plan UX.

    Multi-token only (D10): ``users and sessions``, ``auth middleware``.
    Single-word topics stay out so bare ``redis`` can still mean implement.
    """
    if not _mode_classify_enabled():
        return False
    text = (user_message or "").strip()
    if not text:
        return False
    if looks_like_casual_chat(text) or looks_like_ask_question(text):
        return False
    if "?" in text:
        return False
    if len(text) > 60:
        return False
    # Path / code / fence hints → real coding context
    if re.search(r"[/\\.`]", text) or re.search(r"(?i)\.\w{1,5}\b", text):
        return False
    if (
        has_implement_signal(text)
        or _INVESTIGATE_RE.search(text)
        or _VERIFY_RE.search(text)
        or _REVIEW_RE.search(text)
    ):
        return False

    # Normalize commas to spaces for tokenization
    norm = re.sub(r"[,:;]+", " ", text)
    norm = re.sub(r"\s+", " ", norm).strip()
    tokens = norm.split()
    if len(tokens) < 2 or len(tokens) > 8:
        return False

    content_tokens = 0
    for tok in tokens:
        low = tok.lower()
        if low in _TOPIC_JOINERS:
            continue
        if not _TOPIC_TOKEN_RE.match(tok):
            return False
        content_tokens += 1
    # Need at least two content words, or one content + joiner pattern like "users and sessions"
    if content_tokens < 1:
        return False
    if content_tokens == 1 and len(tokens) < 2:
        return False
    # Require ≥2 content tokens for ASK (D10 multi-token focus)
    if content_tokens < 2:
        return False
    return True


def classify_task_mode(
    edit_format: Optional[str],
    user_message: str = "",
    *,
    intent_mode: Optional[str] = None,
    classifier_model=None,
) -> TaskMode:
    """
    Resolve TaskMode for one user message.

    Priority:
      1. Explicit command via edit_format (ask/context → ASK)
      2. Intent.mode from structured extraction when provided
      3. Conservative prompt heuristics; default IMPLEMENT for coding work,
         but greetings / pure questions / ambiguous topics stay ASK (no plan UX)
      4. If nothing above resolved it and ``classifier_model`` is given
         (duck-typed: needs ``.simple_send_with_retries(messages)``), escalate
         to a bounded weak-model call before falling back to IMPLEMENT.
    """
    fmt = (edit_format or "").strip().lower()
    if fmt in ("ask", "context"):
        # Explicit /ask or /context — hard mapping
        text = user_message or ""
        if intent_mode == "investigate" or _INVESTIGATE_RE.search(text):
            return TaskMode.INVESTIGATE
        return TaskMode.ASK
    if fmt == "plan":
        return TaskMode.PLAN

    text = (user_message or "").strip()

    # Greetings / small-talk win over a stale intent_mode=implement from
    # fabricated clauses (see extract_intent).
    if looks_like_casual_chat(text):
        return TaskMode.ASK

    # Ambiguous noun phrases (``users and sessions``) → ASK before stale implement intent
    if looks_like_ambiguous_topic(text):
        return TaskMode.ASK

    if intent_mode:
        try:
            return TaskMode(intent_mode)
        except ValueError:
            pass

    if not text:
        return TaskMode.IMPLEMENT

    if looks_like_ask_question(text):
        return TaskMode.ASK

    # Explicit read-only / investigate without a competing implement request
    if _INVESTIGATE_RE.search(text) and not (
        _IMPLEMENT_RE.search(text)
        and not re.search(r"(?i)\bdo not\b|\bdon't\b|\bwithout\b", text)
    ):
        # "investigate … do not edit" → INVESTIGATE
        # "why does X fail and can you fix it" has implement signal → IMPLEMENT
        if re.search(r"(?i)\b(fix|implement|change|add|create|update)\b", text) and not re.search(
            r"(?i)\b(do not|don't|without)\s+(?:edit|change|modify|touch|add|create)",
            text,
        ):
            # mixed: question + fix request → IMPLEMENT for the follow-up half
            if re.search(r"(?i)\b(and|then)\s+(?:can you |please )?fix\b", text):
                return TaskMode.IMPLEMENT
        return TaskMode.INVESTIGATE

    if _REVIEW_RE.search(text) and not _IMPLEMENT_RE.search(text):
        return TaskMode.REVIEW

    if _VERIFY_RE.search(text) and not _IMPLEMENT_RE.search(text):
        return TaskMode.VERIFY

    if classifier_model is not None and _mode_classify_enabled():
        escalated = _classify_via_weak_model(text, classifier_model)
        if escalated is not None:
            return escalated

    return TaskMode.IMPLEMENT


def mode_from_edit_format(edit_format: Optional[str]) -> Optional[TaskMode]:
    """Hard mapping for explicit commands only; None if not decisive."""
    fmt = (edit_format or "").strip().lower()
    if fmt in ("ask", "context"):
        return TaskMode.ASK
    if fmt == "plan":
        return TaskMode.PLAN
    return None
