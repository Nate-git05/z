"""Gated planning stage for high-stakes / high-blast-radius tasks.

Reuses existing high_stakes_hit / blast-radius triage — no parallel risk system.
For routine tasks this module is a no-op so the direct-to-diff path stays fast.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence, Tuple

from .architecture import ArchitectureCheckpoint
from .capabilities import CapabilityPlan
from .established_solutions import EstablishedSolutionConsideration
from .journeys import JourneyPlan
from .risk import DetectionSignals, collect_base_signals, scan_high_stakes
from .schema import (
    DEFAULT_BLAST_RADIUS_THRESHOLD,
    RequirementItem,
    TaskChecklist,
    text_looks_high_stakes,
    text_looks_migration,
)


@dataclass
class ValidationContract:
    """Explicit validation contract for one public input (Codex #1)."""

    input_name: str
    domain: str
    on_invalid: str = "raise ValueError"


@dataclass
class AmbiguityResolution:
    """Named ambiguity with a chosen resolution (Codex #10)."""

    ambiguity: str
    resolution: str


@dataclass
class PlanningArtifact:
    """Human-reviewable plan produced before any diff is written."""

    task_id: str
    title: str
    reason: str = ""
    # Human-facing plan body (what the confirm UI must show)
    approach: str = ""
    steps: List[str] = field(default_factory=list)
    out_of_scope: List[str] = field(default_factory=list)
    validation_contracts: List[ValidationContract] = field(default_factory=list)
    # rows: (name, domain, notes)
    input_domain_table: List[Tuple[str, str, str]] = field(default_factory=list)
    invariants: List[str] = field(default_factory=list)
    ambiguities: List[AmbiguityResolution] = field(default_factory=list)
    # Mandatory for non-trivial / established-solution categories: name the
    # stdlib/known approach, or justify a custom implementation.
    established_solutions: List[EstablishedSolutionConsideration] = field(
        default_factory=list
    )
    # Reliability-9 extensions (Codex evaluation priorities)
    capability_plan: Optional[CapabilityPlan] = None
    architecture: Optional[ArchitectureCheckpoint] = None
    journeys: Optional[JourneyPlan] = None
    ux_model: Optional[object] = None
    transition_table: Optional[object] = None
    multi_session_plan: Optional[object] = None
    approved: bool = False
    skipped: bool = False  # True when triage said planning not required

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "reason": self.reason,
            "approach": self.approach,
            "steps": list(self.steps),
            "out_of_scope": list(self.out_of_scope),
            "approved": self.approved,
            "skipped": self.skipped,
            "validation_contracts": [asdict(c) for c in self.validation_contracts],
            "input_domain_table": [
                {"name": n, "domain": d, "notes": note}
                for n, d, note in self.input_domain_table
            ],
            "invariants": list(self.invariants),
            "ambiguities": [asdict(a) for a in self.ambiguities],
            "established_solutions": [asdict(e) for e in self.established_solutions],
            "capability_plan": (
                self.capability_plan.to_dict() if self.capability_plan else None
            ),
            "architecture": (
                self.architecture.to_dict() if self.architecture else None
            ),
            "journeys": self.journeys.to_dict() if self.journeys else None,
            "ux_model": (
                self.ux_model.to_dict()
                if self.ux_model is not None and hasattr(self.ux_model, "to_dict")
                else None
            ),
            "transition_table": (
                self.transition_table.to_dict()
                if self.transition_table is not None
                and hasattr(self.transition_table, "to_dict")
                else None
            ),
            "multi_session_plan": (
                self.multi_session_plan.to_dict()
                if self.multi_session_plan is not None
                and hasattr(self.multi_session_plan, "to_dict")
                else None
            ),
        }


_PUBLIC_INPUT_RE = re.compile(
    r"(?i)\b("
    r"timeout|retries?|max[_ ]?\w+|min[_ ]?\w+|limit|ttl|threshold|tolerance|"
    r"capacity|batch[_ ]?size|workers?|concurrency|port|rate[_ ]?limit|"
    r"api[_ ]?key|token|password|secret|credential|permission|role|"
    r"amount|price|quantity|email|url|path|filename"
    r")\b"
)
_AMBIGUOUS_RE = re.compile(
    r"(?i)\b(somehow|maybe|probably|appropriate|reasonable|as needed|"
    r"etc\.?|and so on|handle (?:it|this|errors?)|make it work|"
    r"similar to|like before|whatever|flexible)\b"
)


def planning_disabled() -> bool:
    return os.environ.get("Z_SKIP_PLANNING", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def planning_forced() -> bool:
    return os.environ.get("Z_FORCE_PLANNING", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _blast_threshold(explicit: Optional[int] = None) -> int:
    if explicit is not None:
        return max(1, int(explicit))
    raw = os.environ.get("Z_BLAST_RADIUS_THRESHOLD", "")
    try:
        return max(1, int(raw)) if raw else DEFAULT_BLAST_RADIUS_THRESHOLD
    except ValueError:
        return DEFAULT_BLAST_RADIUS_THRESHOLD


def triage_for_planning(
    files: Sequence[str],
    *,
    symbols: Sequence[str] = (),
    user_text: str = "",
    reference_count: int = 0,
    blast_radius_threshold: Optional[int] = None,
) -> Tuple[bool, str, DetectionSignals]:
    """
    Decide whether a planning artifact is required before code generation.

    Reuses scan_high_stakes / text_looks_high_stakes / blast_radius_threshold —
    the same signals the uncertainty engine already computes post-edit.
    """
    threshold = _blast_threshold(blast_radius_threshold)
    signals = collect_base_signals(
        files, symbols, blast_radius_threshold=threshold
    )
    signals.reference_count = int(reference_count or 0)

    if planning_disabled():
        return False, "Z_SKIP_PLANNING", signals

    reasons: List[str] = []
    if planning_forced():
        reasons.append("Z_FORCE_PLANNING")
    if signals.high_stakes_hit or signals.migration_hit:
        reasons.append("high_stakes_hit" if signals.high_stakes_hit else "migration_hit")
    if user_text and (
        text_looks_high_stakes(user_text) or text_looks_migration(user_text)
    ):
        if "high_stakes_hit" not in reasons and "migration_hit" not in reasons:
            reasons.append("request_text_high_stakes")
        signals.high_stakes_hit = signals.high_stakes_hit or text_looks_high_stakes(
            user_text
        )
        signals.migration_hit = signals.migration_hit or text_looks_migration(user_text)
    if signals.reference_count >= threshold:
        reasons.append(
            f"blast_radius:{signals.reference_count}>={threshold}"
        )

    # Also treat chat-file symbols that scan as high-stakes
    if not reasons and scan_high_stakes(files, symbols):
        reasons.append("high_stakes_hit")
        signals.high_stakes_hit = True

    # Established-solution categories (IP/email/URL/date/UUID/…) — require the
    # "name the standard or justify custom" plan section before inventing one.
    from .established_solutions import match_request_categories

    est_cats = match_request_categories(user_text or "")
    if est_cats:
        ids = ",".join(c.category_id for c in est_cats[:6])
        reasons.append(f"established_solution:{ids}")

    # Multiplayer / collaborative / browser journeys need planning even when
    # high-stakes path keywords are absent (reliability-9 / CUJ gate).
    from .architecture import architecture_review_needed
    from .journeys import infer_critical_journeys

    if user_text and architecture_review_needed(user_text):
        reasons.append("architecture_review")
    if user_text:
        jp = infer_critical_journeys(user_text)
        if jp.journeys:
            reasons.append(f"critical_journeys:{len(jp.journeys)}")

    if not reasons:
        return False, "", signals
    return True, "; ".join(reasons), signals


def _extract_public_inputs(user_text: str, checklist: Optional[TaskChecklist]) -> List[str]:
    blob = user_text or ""
    if checklist:
        blob += "\n" + "\n".join(i.text for i in checklist.items)
    found: List[str] = []
    seen = set()
    for m in _PUBLIC_INPUT_RE.finditer(blob):
        name = m.group(1).lower().replace(" ", "_")
        # Skip negated mentions ("no email", "without webhook", "not a path")
        start = m.start()
        prefix = blob[max(0, start - 24) : start].lower()
        if re.search(r"(?i)\b(no|not|without|dont|don't|never)\b[\s/,-]*$", prefix):
            continue
        if name not in seen:
            seen.add(name)
            found.append(name)
    return found[:12]


def _default_domain(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("timeout", "ttl", "retries", "limit", "max", "min", "port", "rate", "size", "workers", "concurrency", "amount", "price", "quantity", "threshold", "tolerance", "capacity")):
        return "finite number in documented range; reject <= 0 / NaN where applicable"
    if any(k in n for k in ("email",)):
        return "non-empty valid email string"
    if any(k in n for k in ("url", "path", "filename")):
        return "non-empty path/URL string; reject traversal / empty"
    if any(k in n for k in ("api_key", "token", "password", "secret", "credential")):
        return "non-empty secret string; never log plaintext"
    if any(k in n for k in ("permission", "role")):
        return "explicit allow-list of roles/permissions"
    return "documented non-empty domain; reject invalid immediately"


def _clean_plan_title(planning_text: str, fallback: str = "Implementation plan") -> str:
    """Short human title from intent planning text — never the raw request dump."""
    text = (planning_text or "").strip()
    text = re.sub(r"(?i)^(hi|hello|hey|please|can you|could you)\b[\s,!.]*", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        return fallback
    lower = text.lower()
    if re.search(r"(?i)\b(rpg|text\s*adventure|adventure\s*game)\b", lower):
        return "Text RPG — implementation plan"
    if re.search(r"(?i)\b(cli|command[- ]line)\b.*\bgame\b|\bgame\b.*\bcli\b", lower):
        return "CLI game — implementation plan"
    if re.search(r"(?i)\bmultiplayer\b", lower):
        return "Multiplayer feature — implementation plan"
    if re.search(r"(?i)\b(web\s*app|next\.?js|react)\b", lower):
        return "Web app — implementation plan"
    if re.search(
        r"(?i)\b(slack|discord|teams)\b.*\b(bot|chatbot|app)\b|"
        r"\b(chat\s*bot|chatbot|slack\s*bot|discord\s*bot)\b",
        lower,
    ):
        return "Chatbot — implementation plan"
    if re.search(r"(?i)\b(api|endpoint|backend)\b", lower) and re.search(
        r"(?i)\b(add|create|implement|build|change)\b", lower
    ):
        return "API/backend — implementation plan"
    words = text.split()
    summary = " ".join(words[:8])
    if len(words) > 8:
        summary += "…"
    if len(summary) < 12:
        return fallback
    return f"{summary[0].upper()}{summary[1:]} — plan"


def _draft_approach_and_steps(
    intent,
    *,
    checklist: Optional[TaskChecklist] = None,
) -> Tuple[str, List[str], List[str]]:
    """
    Produce approach + steps from TaskIntent only.

    Template selection reads ``intent.requested_actions`` /
    ``intent.acceptance_criteria`` — never the raw user prompt.
    """
    text = (getattr(intent, "planning_text", None) or "").strip()
    lower = text.lower()
    mode = (getattr(intent, "mode", None) or "implement").lower()
    prohibited = [p.lower() for p in (getattr(intent, "prohibited_actions", None) or [])]
    steps: List[str] = []
    out_of_scope: List[str] = list(getattr(intent, "prohibited_actions", None) or [])

    # Defense in depth: non-implement modes never get implementation templates
    if mode != "implement":
        approach = (
            "Investigate and report findings only — no implementation steps. "
            "Do not create endpoints, UI, or auth changes unless the user "
            "explicitly asks in a follow-up."
        )
        steps = [
            "Inspect the relevant files/symbols named in the request.",
            "Reproduce or localize the reported symptom with read-only checks.",
            "Summarize root cause hypotheses with evidence (paths, lines, commands).",
        ]
        return approach, steps, out_of_scope

    def _prohibited(topic: str) -> bool:
        t = topic.lower()
        return any(t in p for p in prohibited)

    if (
        re.search(r"(?i)\b(rpg|text\s*adventure|adventure)\b", lower)
        or ("game" in lower and re.search(r"(?i)\b(text|cli|terminal|woods|forest)\b", lower))
    ) and not _prohibited("game"):
        approach = (
            "Build a small text RPG as a runnable CLI: rooms/locations, player "
            "state, commands (look/go/inventory/…), and a short starter area "
            "(woods). Keep game rules in a clear module, not scattered prints."
        )
        steps = [
            "Scaffold project layout (game package, entrypoint, README how to run).",
            "Define core models: Player, Location/Room, Item, and world graph.",
            "Implement command parser + game loop (look, go, take, inventory, quit).",
            "Author a small woods starting area with 3–6 connected locations.",
            "Add win/lose or quest hook so a short playthrough is completable.",
            "Add unit tests for movement, inventory, and invalid commands.",
            "Verify: run the game once end-to-end from a clean start.",
        ]
        out_of_scope.extend(
            [
                "Graphics / GUI (text-only unless you ask).",
                "Multiplayer / networking.",
                "Save-game persistence across sessions (unless you ask).",
            ]
        )
    elif re.search(r"(?i)\b(multiplayer|two\s+players?|lobby)\b", lower) and not _prohibited(
        "multiplayer"
    ):
        approach = (
            "Implement shared server state with clear UI ↔ API ↔ service ↔ "
            "repository boundaries; prove the two-user journey with the right "
            "evidence type before claiming done."
        )
        steps = [
            "Architecture checkpoint: state ownership, auth, concurrency.",
            "Domain/service layer + repository/state adapter.",
            "Typed API routes with validation and auth.",
            "UI states for lobby / challenge / match / results.",
            "Unit + integration tests; plan multi-session E2E evidence.",
        ]
    elif (
        re.search(r"(?i)\b(add|create|implement|build|change|update)\b", lower)
        and re.search(r"(?i)\b(api|endpoint|backend|server)\b", lower)
        and not _prohibited("api")
        and not _prohibited("endpoint")
    ):
        approach = (
            "Add or change backend endpoints with explicit schemas, validation, "
            "and tests against the real state layer."
        )
        steps = [
            "Identify routes and request/response contracts.",
            "Implement service logic + validation at the boundary.",
            "Wire auth/authorization if the surface is protected.",
            "Add integration tests for success and error paths.",
        ]
    elif re.search(
        r"(?i)\b(slack|discord|teams)\b.*\b(bot|chatbot|app)\b|"
        r"\b(chat\s*bot|chatbot|slack\s*bot|discord\s*bot)\b|"
        r"\bbot\b.*\b(slack|discord|teams)\b",
        lower,
    ) and not _prohibited("bot"):
        approach = (
            "Build a small workspace chatbot with config, authenticated "
            "event handling, one real reply path, and a local smoke run — "
            "not a vague “bot somewhere.”"
        )
        steps = [
            "Scaffold project layout (entrypoint, config for tokens/secrets, README how to run).",
            "Wire Slack/Discord client (Socket Mode or HTTP Events webhook).",
            "Handle a core event (app_mention / message) with a deterministic reply.",
            "Add routing for 1–2 useful commands beyond echo.",
            "Validate requests (signing secret) and fail closed on bad auth.",
            "Add unit tests for handlers; document test-workspace setup.",
            "Smoke: start the bot and verify one end-to-end reply.",
        ]
        out_of_scope.extend(
            [
                "Marketplace / App Directory publish (unless you ask).",
                "Multi-workspace SaaS billing or admin UI (unless you ask).",
                "Heavy LLM agent features beyond a simple reply (unless you ask).",
            ]
        )
    elif re.search(r"(?i)\b(fix|bug|broken|error|failing)\b", lower):
        approach = (
            "Diagnose from the reported symptom, find the earliest unsupported "
            "assumption, fix root cause (not the detector), and re-run the "
            "original failing check."
        )
        steps = [
            "Reproduce the failure and capture exact command/output.",
            "Classify failure layer (env / type / assertion / …).",
            "Locate root cause; avoid weakening tests or typecheck.",
            "Apply minimal fix; re-run the original verification.",
        ]
    elif re.search(
        r"(?i)\b(logwatch|log\s*tail|tail(s|ing)?\s+logs?|rolling\s+(?:time\s+)?window|"
        r"cli\s+tool|command[- ]line\s+tool)\b",
        lower,
    ) and not _prohibited("cli"):
        approach = (
            "Build a focused log-watching CLI: stream inputs, match rules, "
            "count hits in a rolling window, and alert on stderr — with tests "
            "for matcher + window math."
        )
        steps = [
            "Scaffold CLI project (entrypoint, args: files, rules, threshold N, window).",
            "Implement follow/tail for one or more log files (incl. rotate-safe read).",
            "Rule engine: regex and plain-substring matchers with clear errors.",
            "Rolling-window counters per rule; alert to stderr when N is exceeded.",
            "Unit tests for matcher + window; smoke the CLI on a sample log.",
        ]
        if re.search(r"(?i)\b(no\s+email|without\s+email|no\s+webhook|without\s+webhook)\b", lower):
            out_of_scope.extend(
                [
                    "Email / webhook / pager alerts (stderr-only for now).",
                ]
            )
    else:
        # Prefer concrete requested actions as steps when they are specific.
        # Never fall through to "Do: <entire vague request>" — that is what
        # made the plan/checklist UI look like an echo of the user prompt.
        actions = list(getattr(intent, "requested_actions", None) or [])
        vague_build = bool(
            actions
            and len(actions) == 1
            and len(actions[0]) < 90
            and re.search(
                r"(?i)\b(build|create|make|implement|write|scaffold)\b",
                actions[0],
            )
            and not re.search(
                r"(?i)\b(test|fix|bug|endpoint|route|function|class|file)\b",
                actions[0],
            )
        )
        if actions and not vague_build:
            approach = (
                "Implement the requested actions incrementally with tests, "
                "respecting explicit exclusions."
            )
            steps = [f"Do: {a}" for a in actions[:8]]
            steps.append("Run project checks and smoke the primary path.")
        else:
            approach = (
                "Break the request into concrete deliverables, implement "
                "incrementally with tests, and verify the primary user path before "
                "claiming completion."
            )
            steps = [
                "Name what “done” looks like (user-visible path + how to run it).",
                "Scaffold project layout / entrypoint and config.",
                "Implement the core happy path first; defer extras.",
                "Add focused tests for the main path and one failure case.",
                "Run project checks and a manual smoke of the primary path.",
            ]

    # Fold checklist product items — but never prohibited topics
    if checklist:
        for item in checklist.items[:6]:
            kind = getattr(item, "kind", "product") or "product"
            if kind in ("product", "quality") and item.text.strip():
                candidate = item.text.strip()
                if any(p and p in candidate.lower() for p in prohibited):
                    continue
                if candidate.lower() not in {s.lower() for s in steps}:
                    if len(candidate) < 120 and candidate.lower()[:40] not in lower[:40]:
                        steps.append(f"Cover requirement: {candidate}")

    # Strip any step that names a prohibited topic
    filtered = []
    for s in steps:
        sl = s.lower()
        if any(
            t in sl
            for t in ("auth", "authentication", "ui", "concurrency", "production")
            if _prohibited(t)
        ):
            continue
        filtered.append(s)
    steps = filtered or steps[:1]

    return approach, steps[:10], out_of_scope[:12]


def draft_plan_from_request(
    user_message: str,
    *,
    title: str = "",
    checklist: Optional[TaskChecklist] = None,
    reason: str = "",
    files: Sequence[str] = (),
    skill_capabilities: Sequence[str] = (),
    skill_ids: Sequence[str] = (),
    intent=None,
) -> PlanningArtifact:
    """
    Build a mechanical planning skeleton from structured TaskIntent.

    Template selection and steps come from intent fields only — never from
    raw-prompt keyword scans in this module.
    """
    from .intent import extract_intent

    if intent is None:
        intent = extract_intent(user_message or "")

    # Defense in depth: non-implement → no implementation plan artifact steps
    if (getattr(intent, "mode", None) or "").lower() != "implement":
        task_id = (checklist.task_id if checklist else None) or str(uuid.uuid4())
        approach, steps, out_of_scope = _draft_approach_and_steps(
            intent, checklist=checklist
        )
        return PlanningArtifact(
            task_id=task_id,
            title="Investigation — no implementation plan",
            reason=reason or f"intent.mode={intent.mode}",
            approach=approach,
            steps=steps,
            out_of_scope=out_of_scope,
            approved=False,
            skipped=True,
        )

    task_id = (checklist.task_id if checklist else None) or str(uuid.uuid4())
    raw_title = title or (checklist.title if checklist else "") or ""
    planning_text = intent.planning_text or ""
    title = _clean_plan_title(planning_text or raw_title, fallback=raw_title[:60] or "Task plan")
    # Public inputs: only from requested actions / acceptance (not observations)
    inputs = _extract_public_inputs(planning_text, checklist)

    approach, steps, out_of_scope = _draft_approach_and_steps(
        intent, checklist=checklist
    )

    # P1.1 — active constraint check: drop / flag steps that violate constraints
    try:
        from .clause import clause_violates_constraints

        clauses = list(getattr(intent, "clauses", None) or [])
        if checklist is not None:
            clauses = clauses or list(getattr(checklist, "clauses", None) or [])
        filtered_steps = []
        for step in steps:
            viol = clause_violates_constraints(step, clauses)
            if viol is not None:
                out_of_scope.append(
                    f"Blocked by constraint ({viol.text}): refused step «{step}»"
                )
                continue
            filtered_steps.append(step)
        steps = filtered_steps or [
            "No safe steps remain under active constraints — clarify the request."
        ]
    except Exception:
        pass

    contracts = [
        ValidationContract(
            input_name=name,
            domain=_default_domain(name),
            on_invalid="raise ValueError (fail loud at construction / entry)",
        )
        for name in inputs
    ]
    table = [
        (c.input_name, c.domain, "public input inferred from request")
        for c in contracts
    ]

    invariants: List[str] = []
    # Prefer concrete product invariants from checklist/steps — not the raw
    # full user message dumped as a single invariant.
    if checklist:
        for item in checklist.items:
            kind = getattr(item, "kind", "product") or "product"
            if kind in ("product", "quality") and item.text.strip():
                t = item.text.strip()
                if len(t) <= 160:
                    invariants.append(t)
    for step in steps[:4]:
        if step not in invariants:
            invariants.append(step)

    # Always name fail-loud / no-fabrication when high-stakes triage fired
    boilerplate = [
        "Invalid public inputs are rejected at the boundary (no limp-forward defaults).",
        "Do not fabricate local stand-ins for missing third-party packages.",
        "Do not weaken typecheck/tests/CI/lint to go green without human approval.",
        "Do not claim completion without correctly typed evidence for critical journeys.",
    ]
    for b in boilerplate:
        if b not in invariants:
            invariants.append(b)

    ambiguities: List[AmbiguityResolution] = []
    for m in _AMBIGUOUS_RE.finditer(planning_text or ""):
        phrase = m.group(0)
        ambiguities.append(
            AmbiguityResolution(
                ambiguity=f"Request uses vague phrasing: '{phrase}'",
                resolution=(
                    "Treat as requiring an explicit, testable behavior; "
                    "prefer fail-loud over silent fallback."
                ),
            )
        )
    # Surface prohibitions as binding resolutions
    for proh in getattr(intent, "prohibited_actions", None) or []:
        ambiguities.append(
            AmbiguityResolution(
                ambiguity=f"User prohibited: {proh}",
                resolution="Do not plan or implement this; treat as out of scope.",
            )
        )
    if files:
        ambiguities.append(
            AmbiguityResolution(
                ambiguity=f"Edit scope includes: {', '.join(list(files)[:8])}",
                resolution="Limit changes to named files unless a dependency forces a wider edit.",
            )
        )
    if not ambiguities:
        ambiguities.append(
            AmbiguityResolution(
                ambiguity="No explicit edge-case list in the request",
                resolution=(
                    "Enumerate empty/zero/negative/null inputs for each public "
                    "parameter and add at least one rejecting test."
                ),
            )
        )

    from .established_solutions import considerations_from_text

    blob_for_est = planning_text or ""
    if checklist:
        blob_for_est += "\n" + "\n".join(i.text for i in checklist.items)
    established = considerations_from_text(blob_for_est)

    # Always surface the established-solution question on gated plans so it
    # cannot be silently skipped under pressure — even when no category matched.
    if not established:
        established = [
            EstablishedSolutionConsideration(
                category_id="general",
                problem_category=(
                    "Any well-known problem (parsing, data structure, concurrency, …)"
                ),
                standard_approach="",
                decision="unspecified",
                custom_justification="",
            )
        ]

    est_invariant = (
        "For each established-solution category: use the named standard "
        "approach (stdlib / known algorithm), or record an explicit custom "
        "justification — do not invent a parser/structure from scratch silently."
    )
    if est_invariant not in invariants:
        invariants.append(est_invariant)

    # --- Reliability-9: capabilities / architecture / journeys ---------------
    from .architecture import draft_architecture_checkpoint
    from .assertions import infer_transition_table
    from .browser_sessions import draft_multi_session_plan
    from .capabilities import build_capability_plan
    from .journeys import infer_critical_journeys
    from .ux_states import draft_ux_model

    # Architecture / journeys still need descriptive context; capabilities
    # must come from classified intent only (P0.3).
    req_blob = planning_text or blob_for_est or ""
    cap_plan = build_capability_plan(
        intent=intent,
        skill_capabilities=skill_capabilities,
        skill_ids=skill_ids,
    )
    arch = draft_architecture_checkpoint(req_blob)
    journeys = infer_critical_journeys(req_blob)
    ux = draft_ux_model(req_blob)
    transitions = infer_transition_table(req_blob)
    multi = draft_multi_session_plan(
        req_blob,
        journey=journeys.journeys[0] if journeys.journeys else None,
    )

    for tip in cap_plan.compensation:
        if tip not in invariants:
            invariants.append(tip[:200])

    return PlanningArtifact(
        task_id=task_id,
        title=title,
        reason=reason,
        approach=approach,
        steps=steps,
        out_of_scope=out_of_scope,
        validation_contracts=contracts,
        input_domain_table=table,
        invariants=invariants[:24],
        ambiguities=ambiguities[:8],
        established_solutions=established[:8],
        capability_plan=cap_plan,
        architecture=arch if arch.items else None,
        journeys=journeys if journeys.journeys else None,
        ux_model=ux if ux.applicable else None,
        transition_table=transitions,
        multi_session_plan=multi if multi.required else None,
        approved=False,
        skipped=False,
    )


def format_plan_for_user(plan: PlanningArtifact) -> str:
    lines = [
        f"Implementation plan: {plan.title}",
        f"Why planning: {plan.reason or '(unspecified)'}",
        "",
    ]
    if plan.approach:
        lines.append("Approach:")
        lines.append(f"  {plan.approach}")
        lines.append("")
    if plan.steps:
        lines.append("Steps:")
        for i, step in enumerate(plan.steps, 1):
            lines.append(f"  {i}. {step}")
        lines.append("")
    if plan.out_of_scope:
        lines.append("Out of scope (unless you say otherwise):")
        for item in plan.out_of_scope:
            lines.append(f"  - {item}")
        lines.append("")

    lines.append("Validation contracts (per public input):")
    if plan.validation_contracts:
        for c in plan.validation_contracts:
            lines.append(
                f"  - {c.input_name}: domain={c.domain}; on_invalid={c.on_invalid}"
            )
    else:
        lines.append("  - (none inferred — add any new public inputs explicitly)")

    lines.append("")
    lines.append("Input-domain table:")
    if plan.input_domain_table:
        lines.append("  | name | domain | notes |")
        for name, domain, notes in plan.input_domain_table:
            lines.append(f"  | {name} | {domain} | {notes} |")
    else:
        lines.append("  (empty)")

    lines.append("")
    lines.append("Named invariants:")
    for inv in plan.invariants:
        lines.append(f"  - {inv}")

    lines.append("")
    lines.append("Ambiguities → chosen resolution:")
    for a in plan.ambiguities:
        lines.append(f"  - {a.ambiguity}")
        lines.append(f"    → {a.resolution}")

    lines.append("")
    lines.append(
        "Established solutions (required — name the standard approach, "
        "or justify custom):"
    )
    if plan.established_solutions:
        for e in plan.established_solutions:
            lines.append(f"  - [{e.category_id}] {e.problem_category}")
            if e.standard_approach:
                lines.append(f"      Prefer: {e.standard_approach}")
            lines.append(
                "      Decision: use standard (name it) OR custom because: …"
            )
            if e.decision and e.decision != "unspecified":
                lines.append(f"      Recorded: {e.decision}")
                if e.custom_justification:
                    lines.append(f"      Justification: {e.custom_justification}")
    else:
        lines.append(
            "  - (none inferred — still ask: is any part a solved stdlib/"
            "algorithm problem?)"
        )

    if plan.capability_plan:
        lines.append("")
        from .capabilities import format_capability_plan

        lines.append(format_capability_plan(plan.capability_plan))

    if plan.architecture:
        lines.append("")
        from .architecture import format_architecture_checkpoint

        lines.append(format_architecture_checkpoint(plan.architecture))

    if plan.journeys:
        lines.append("")
        from .journeys import format_journey_plan

        lines.append(format_journey_plan(plan.journeys))

    if plan.ux_model:
        lines.append("")
        from .ux_states import format_ux_model

        lines.append(format_ux_model(plan.ux_model))

    if plan.transition_table:
        lines.append("")
        from .assertions import format_transition_table

        lines.append(format_transition_table(plan.transition_table))

    if plan.multi_session_plan:
        lines.append("")
        from .browser_sessions import format_multi_session

        lines.append(format_multi_session(plan.multi_session_plan))

    lines.append("")
    lines.append(
        "Confirm this plan to proceed. Reject to stop before any diff is written."
    )
    return "\n".join(lines)


def format_plan_for_confirm(plan: PlanningArtifact) -> str:
    """
    Compact plan body for the orange escalation panel.

    Must never be just the raw user request — always approach + numbered steps.
    """
    lines = [plan.title or "Implementation plan", ""]
    if plan.approach:
        lines.append(plan.approach.strip())
        lines.append("")
    if plan.steps:
        lines.append("Steps:")
        for i, step in enumerate(plan.steps, 1):
            lines.append(f"  {i}. {step}")
    else:
        lines.append("Steps: (none drafted — reject and clarify the request)")
    if plan.out_of_scope:
        lines.append("")
        lines.append("Out of scope:")
        for item in plan.out_of_scope[:4]:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def format_thin_confirm(plan: PlanningArtifact, checklist=None) -> str:
    """Compact thin-checklist confirm body (no scrollback wall)."""
    lines = [format_plan_for_confirm(plan)]
    items = list(getattr(checklist, "items", None) or [])
    tracking = []
    for item in items[:7]:
        text = (getattr(item, "text", None) or "").strip()
        if not text or text.lower().startswith("do:"):
            continue
        tracking.append(text)
    if tracking:
        lines.append("")
        lines.append("Tracking:")
        for i, text in enumerate(tracking, 1):
            lines.append(f"  {i}. {text}")
    return "\n".join(lines)


def format_plan_for_context(plan: PlanningArtifact) -> str:
    """
    Inject into cur_messages as grounding for implementation.

    Detectors use ``engine.ctx.plan`` (full artifact). By default the coding
    turn gets a compact directive; set ``Z_PLAN_CONTEXT_FULL=1`` or
    ``Z_CONTROL_PLANE_COMPACT=0`` for the legacy full dump.
    """
    try:
        from aider.z.control_plane_budget import (
            control_plane_compact_enabled,
            format_plan_directive,
            plan_context_full_enabled,
        )

        if control_plane_compact_enabled() and not plan_context_full_enabled():
            return format_plan_directive(plan)
    except Exception:
        pass
    return _format_plan_for_context_full(plan)


def _format_plan_for_context_full(plan: PlanningArtifact) -> str:
    """Legacy full plan dump into cur_messages (escape hatch)."""
    lines = [
        "# Approved implementation plan (binding)",
        f"Task: {plan.title}",
        f"Why gated: {plan.reason}",
        "",
    ]
    if plan.approach:
        lines.append("## Approach")
        lines.append(plan.approach)
        lines.append("")
    if plan.steps:
        lines.append("## Steps")
        for i, step in enumerate(plan.steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")
    if plan.out_of_scope:
        lines.append("## Out of scope")
        for item in plan.out_of_scope:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("## Validation contracts")
    for c in plan.validation_contracts:
        lines.append(f"- `{c.input_name}`: {c.domain} / {c.on_invalid}")
    if not plan.validation_contracts:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Input-domain table")
    for name, domain, notes in plan.input_domain_table:
        lines.append(f"- {name}: {domain} ({notes})")
    lines.append("")
    lines.append("## Invariants (must hold in the diff)")
    for inv in plan.invariants:
        lines.append(f"- {inv}")
    lines.append("")
    lines.append("## Ambiguity resolutions")
    for a in plan.ambiguities:
        lines.append(f"- {a.ambiguity} → {a.resolution}")
    lines.append("")
    lines.append("## Established solutions (binding)")
    lines.append(
        "If the task involves a well-known problem (IP/email/URL/date/UUID "
        "parsing, heaps/caches, concurrency primitives), use the language "
        "stdlib or a known algorithm. Inventing a custom parser/structure "
        "requires an explicit custom justification in this plan."
    )
    for e in plan.established_solutions:
        prefer = e.standard_approach or "(name the standard)"
        lines.append(
            f"- [{e.category_id}] {e.problem_category}: prefer `{prefer}` "
            f"(decision={e.decision or 'unspecified'})"
        )
        if e.custom_justification:
            lines.append(f"  custom justification: {e.custom_justification}")
    if plan.capability_plan:
        from .capabilities import format_capability_plan

        lines.append("")
        lines.append("## Capability plan")
        lines.append(format_capability_plan(plan.capability_plan))
    if plan.architecture:
        from .architecture import format_architecture_checkpoint

        lines.append("")
        lines.append("## Architecture checkpoint")
        lines.append(format_architecture_checkpoint(plan.architecture))
    if plan.journeys:
        from .journeys import format_journey_plan

        lines.append("")
        lines.append("## Critical user journeys")
        lines.append(format_journey_plan(plan.journeys))
    if plan.ux_model:
        from .ux_states import format_ux_model

        lines.append("")
        lines.append("## UX visible-state model")
        lines.append(format_ux_model(plan.ux_model))
    if plan.transition_table:
        from .assertions import format_transition_table, generate_transition_tests

        lines.append("")
        lines.append("## State transition table")
        lines.append(format_transition_table(plan.transition_table))
        lines.append("")
        lines.append("## Suggested exact-assertion tests")
        lines.append(generate_transition_tests(plan.transition_table))
    if plan.multi_session_plan:
        from .browser_sessions import format_multi_session

        lines.append("")
        lines.append("## Multi-session browser plan")
        lines.append(format_multi_session(plan.multi_session_plan))
    lines.append("")
    lines.append(
        "Implement only what this plan authorizes. "
        "Detectors will check the diff against these invariants and against "
        "the established-solutions taxonomy. "
        "Never weaken verification to go green. "
        "Never claim completion without correctly typed journey evidence."
    )
    return "\n".join(lines)


def merge_plan_invariants_into_checklist(
    checklist: TaskChecklist,
    plan: PlanningArtifact,
) -> TaskChecklist:
    """
    Fold named invariants into the requirement ledger as quality items so
    bind_evidence / detect_requirement_gaps can check them mechanically.
    """
    if not checklist or not plan or not plan.invariants:
        return checklist
    existing = {i.text.strip().lower() for i in checklist.items}
    for inv in plan.invariants:
        key = inv.strip().lower()
        if not key or key in existing:
            continue
        # Skip boilerplate that is process-style guidance
        kind = "quality"
        if "fabricate" in key or "third-party" in key:
            kind = "process"
        checklist.items.append(
            RequirementItem(
                text=inv.strip(),
                kind=kind,
                status="Not Addressed",
            )
        )
        existing.add(key)
    return checklist


def plan_invariant_texts(plan: Optional[PlanningArtifact]) -> List[str]:
    if not plan or plan.skipped or not plan.approved:
        return []
    return [i for i in plan.invariants if i and i.strip()]


_LANG_FEEDBACK_RE = re.compile(
    r"(?i)\b("
    r"rust|python|typescript|javascript|node\.?js|golang|go|"
    r"c\+\+|cpp|c#|csharp|java|kotlin|swift|ruby|php|scala|elixir|zig|haskell"
    r")\b"
)

_LANG_DISPLAY = {
    "node.js": "Node.js",
    "nodejs": "Node.js",
    "golang": "Go",
    "go": "Go",
    "c++": "C++",
    "cpp": "C++",
    "c#": "C#",
    "csharp": "C#",
    "rust": "Rust",
    "python": "Python",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "java": "Java",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "ruby": "Ruby",
    "php": "PHP",
    "scala": "Scala",
    "elixir": "Elixir",
    "zig": "Zig",
    "haskell": "Haskell",
}


def _normalize_lang(raw: str) -> str:
    key = (raw or "").strip().lower().replace("node.js", "nodejs")
    if key == "nodejs":
        return "Node.js"
    return _LANG_DISPLAY.get(key, (raw or "").strip().title())


def _scrub_feedback_echo_steps(plan: PlanningArtifact, feedback: str) -> None:
    """Drop steps that are just the raw Change text glued on as Do: …"""
    fb = (feedback or "").strip().lower()
    if not fb:
        return
    fb_compact = re.sub(r"\s+", " ", fb)
    kept: List[str] = []
    for s in plan.steps:
        sl = re.sub(r"\s+", " ", (s or "").strip().lower())
        body = re.sub(r"(?i)^\s*do:\s*", "", sl).strip()
        if body == fb_compact or sl == fb_compact:
            continue
        if body.startswith(fb_compact[:48]) and len(fb_compact) >= 24:
            continue
        if "user plan revisions" in body:
            continue
        kept.append(s)
    plan.steps = kept or plan.steps[:1]


def _refresh_invariants_from_steps(plan: PlanningArtifact) -> None:
    """Rebuild product invariants from concrete steps (drop echo Do: lines)."""
    boilerplate = [
        "Invalid public inputs are rejected at the boundary (no limp-forward defaults).",
        "Do not fabricate local stand-ins for missing third-party packages.",
        "Do not weaken typecheck/tests/CI/lint to go green without human approval.",
        "Do not claim completion without correctly typed evidence for critical journeys.",
        "For each established-solution category: use the named standard "
        "approach (stdlib / known algorithm), or record an explicit custom "
        "justification — do not invent a parser/structure from scratch silently.",
    ]
    product: List[str] = []
    for step in (plan.steps or [])[:6]:
        t = (step or "").strip()
        if not t or t.lower().startswith("do:"):
            continue
        if len(t) <= 160 and t not in product:
            product.append(t)
    for b in boilerplate:
        if b not in product:
            product.append(b)
    # Keep quality invariants already patched in
    for inv in plan.invariants or []:
        if inv and inv not in product and (
            "production" in inv.lower()
            or "meta" in inv.lower()
            or "rust" in inv.lower()
            or inv.startswith("Language:")
        ):
            product.insert(0, inv)
    plan.invariants = product


def _apply_feedback_patches(plan: PlanningArtifact, feedback: str) -> None:
    """Keyword patches on top of a re-drafted plan (mutates ``plan``)."""
    low = (feedback or "").lower()
    if not low.strip():
        return

    def _upsert_step(text: str, *, near: str = "", at: int = 1) -> None:
        key = text.lower()
        if any(key[:40] in (s or "").lower() for s in plan.steps):
            return
        if near:
            for i, s in enumerate(plan.steps):
                if near in (s or "").lower():
                    plan.steps[i] = text
                    return
        plan.steps.insert(min(max(0, at), len(plan.steps)), text)

    def _drop_steps_matching(*needles: str) -> None:
        plan.steps = [
            s
            for s in plan.steps
            if not any(n in (s or "").lower() for n in needles)
        ]

    def _ensure_oos(text: str) -> None:
        if any(text.lower() in (o or "").lower() for o in plan.out_of_scope):
            return
        plan.out_of_scope.append(text)

    _scrub_feedback_echo_steps(plan, feedback)

    if re.search(r"(?i)\bsocket\s*mode\b", low):
        _upsert_step(
            "Use Slack Socket Mode (no public HTTP webhook required).",
            near="socket",
        )
        plan.approach = re.sub(
            r"(?i)socket mode or http events webhook",
            "Socket Mode",
            plan.approach or "",
        ) or plan.approach
    if re.search(r"(?i)\b(webhook|events\s*api|http\s*endpoint)\b", low) and not re.search(
        r"(?i)\bsocket\s*mode\b", low
    ):
        _upsert_step(
            "Expose an HTTP Events API webhook with signing-secret verification.",
            near="webhook",
        )
    lang_m = _LANG_FEEDBACK_RE.search(low)
    if lang_m:
        lang = _normalize_lang(lang_m.group(1))
        lang_step = (
            f"Implement in {lang} "
            f"({'Cargo workspace, idiomatic Rust, clippy+tests' if lang == 'Rust' else 'idiomatic project layout + tests'})."
        )
        _upsert_step(lang_step, near="implement in", at=0)
        # Lead approach with the language — don't append a weak Prefer …
        ap = (plan.approach or "").strip()
        ap = re.sub(
            r"(?i)^\s*(build in|implement in|prefer)\s+\w+[\s.—-]*",
            "",
            ap,
        ).strip()
        plan.approach = f"Build in {lang}. {ap}".strip()
        # Drop leftover giant Do: echo steps — template steps already cover them
        plan.steps = [
            s
            for s in plan.steps
            if not (
                (s or "").lower().startswith("do:")
                and len(s) > 100
            )
        ] or plan.steps
        if not any(lang.lower() in (s or "").lower() for s in plan.steps):
            plan.steps.insert(0, lang_step)
        plan.invariants = [
            i
            for i in (plan.invariants or [])
            if not (i or "").lower().startswith("do:")
        ]
        lang_inv = f"Language: {lang} (per user revision)."
        if lang_inv not in plan.invariants:
            plan.invariants.insert(0, lang_inv)
    if re.search(
        r"(?i)\b(meta|faang|production[- ]grade|production\s+quality|"
        r"senior|rigorous|software\s+engineers?\s+for)\b",
        low,
    ):
        _upsert_step(
            "Production bar: clear module boundaries, typed errors, tests, "
            "and docs a Meta-level reviewer would accept.",
            near="production bar",
            at=1,
        )
        q_inv = (
            "Meet a production engineering bar: no silent failures, "
            "explicit error paths, and evidence before claiming done."
        )
        if q_inv not in (plan.invariants or []):
            plan.invariants.insert(0, q_inv)
        ap = (plan.approach or "").strip()
        if "production" not in ap.lower():
            plan.approach = (
                f"{ap} Hold a production / Meta-style engineering bar."
            ).strip()
    if re.search(r"(?i)\b(no\s+llm|without\s+llm|dont\s+use\s+llm|don't\s+use\s+llm)\b", low):
        _ensure_oos("LLM / generative replies (user forbade).")
        _drop_steps_matching("llm")
    if re.search(r"(?i)\b(slash\s*command|/\w+)\b", low):
        _upsert_step("Register and handle slash command(s) named by the user.")
    if re.search(r"(?i)\b(no\s+tests?|skip\s+tests?|without\s+tests?)\b", low):
        _drop_steps_matching("unit test", "add unit", "add focused test")
        _ensure_oos("Automated tests deferred per user revision (verify manually).")
    if re.search(r"(?i)\b(bolt|block\s*kit)\b", low):
        _upsert_step("Use Bolt/Block Kit for interactive messages where useful.")

    _scrub_feedback_echo_steps(plan, feedback)
    _refresh_invariants_from_steps(plan)


def revise_plan_with_feedback(
    plan: PlanningArtifact,
    feedback: str,
    *,
    original_request: str = "",
) -> PlanningArtifact:
    """
    Revise an implementation plan from natural-language user feedback.

    Re-drafts from the **original request only** (never treats Change text as a
    new product action), then applies structured patches (language, quality bar,
    socket/webhook, …). Feedback is recorded as an ambiguity resolution.
    """
    feedback = (feedback or "").strip()
    if not feedback or plan is None:
        return plan

    # Critical: do NOT concatenate feedback into the request — that produced
    # "Do: build this in rust…" echo steps and left the plan unchanged.
    base = (original_request or "").strip()
    if not base:
        # Fall back to prior planning text without the revision echo
        base = (plan.title or "").replace(" — plan", "").strip()
    if not base:
        base = feedback

    revised = draft_plan_from_request(
        base,
        title=plan.title or "",
        reason=(plan.reason or "") + ";user_revise",
    )
    revised.task_id = plan.task_id or revised.task_id
    # Carry forward prior resolutions + this feedback
    revised.ambiguities = list(plan.ambiguities or []) + [
        AmbiguityResolution(
            ambiguity="User revised the implementation plan",
            resolution=feedback,
        )
    ]
    # Keep structure if re-draft dropped it — but never stale Do:/contracts
    if plan.established_solutions and not revised.established_solutions:
        revised.established_solutions = list(plan.established_solutions)
    if plan.architecture and not revised.architecture:
        revised.architecture = plan.architecture
    if plan.capability_plan and not revised.capability_plan:
        revised.capability_plan = plan.capability_plan
    # Do NOT carry validation_contracts / invariants from the old plan —
    # they often echo the pre-revision Do: dump or false-positive inputs.

    _apply_feedback_patches(revised, feedback)
    revised.approved = False
    return revised


def show_full_plan_view(io, plan: PlanningArtifact) -> None:
    """
    Show the full plan in an orange escalation panel and wait for Enter.

    Without a pause, View printed then immediately re-opened the compact confirm,
    scrolling the plan away (felt like a timeout).
    """
    body = format_plan_for_user(plan)
    pretty = bool(getattr(io, "pretty", True))
    z_theme = bool(getattr(io, "z_theme", False))
    try:
        if z_theme and pretty:
            from aider.z.escalation import render_escalation

            console = getattr(io, "console", None)
            render_escalation(
                "Full implementation plan",
                console=console,
                context=body,
                pretty=True,
                accent_context=True,
            )
        else:
            warn = getattr(io, "tool_warning", None)
            out = getattr(io, "tool_output", None)
            if warn:
                warn("—— Full implementation plan ——")
            if out:
                out("")
                out(body)
                out("")
    except Exception:
        out = getattr(io, "tool_output", None)
        if out:
            out(body)

    # Pause so the developer can read before the compact Y/N/C/V returns.
    if getattr(io, "yes", None) in (True, False):
        return
    prompt_ask = getattr(io, "prompt_ask", None)
    if prompt_ask:
        try:
            prompt_ask("Press Enter when done reading the plan", default="")
        except Exception:
            pass
    else:
        try:
            input("Press Enter when done reading the plan ")
        except Exception:
            pass


def interactive_plan_confirm(
    io,
    plan: PlanningArtifact,
    *,
    question: str = "Proceed with this implementation plan?",
    original_request: str = "",
    max_rounds: int = 4,
    confirm_subject: str | None = None,
    checklist=None,
) -> tuple:
    """
    Y/N/C/V confirm loop for an implementation plan.

    Returns ``(approved: bool, plan)``. On Change, collects free-text
    revisions, revises the plan, and re-asks with a compact panel.
    View shows the full plan in an orange panel and waits for Enter
    (does not consume a change round).
    """
    if plan is None:
        return False, plan

    # Non-interactive / yes-always: approve as-is
    if getattr(io, "yes", None) is True:
        return True, plan
    if getattr(io, "yes", None) is False:
        return False, plan

    current = plan
    ask = getattr(io, "plan_confirm_ask", None)
    changes_used = 0
    subject_override = confirm_subject
    while changes_used < max(1, max_rounds):
        if subject_override is not None:
            subject = subject_override
            subject_override = None
        elif checklist is not None:
            subject = format_thin_confirm(current, checklist)
        else:
            subject = format_plan_for_confirm(current)
        if ask:
            choice = ask(question, subject=subject, default="y")
        else:
            # Fallback for test doubles without plan_confirm_ask
            ok = bool(io.confirm_ask(question, default="y", subject=subject))
            choice = "yes" if ok else "no"

        if choice == "view":
            show_full_plan_view(io, current)
            continue

        if choice == "yes":
            return True, current
        if choice == "no":
            return False, current

        # change
        changes_used += 1
        pending = getattr(io, "_pending_plan_change", None)
        if pending:
            feedback = str(pending).strip()
            try:
                io._pending_plan_change = None
            except Exception:
                pass
        else:
            feedback = ""
            prompt_ask = getattr(io, "prompt_ask", None)
            if prompt_ask:
                io.tool_output(
                    "Tell Z what to change (stack, Socket Mode vs webhook, "
                    "commands, out-of-scope, language, …)."
                )
                feedback = (prompt_ask("Plan changes:") or "").strip()
        if not feedback:
            io.tool_warning("No changes given — keeping the current plan.")
            continue
        try:
            io.tool_output(f"Revising plan from: {feedback[:200]}")
        except Exception:
            pass
        current = revise_plan_with_feedback(
            current,
            feedback,
            original_request=original_request,
        )
        # Compact re-confirm with the *updated* thin subject — do not dump the
        # full plan wall (View still available). One status line of what changed.
        try:
            lang_hint = ""
            m = _LANG_FEEDBACK_RE.search(feedback)
            if m:
                lang_hint = f" → {_normalize_lang(m.group(1))}"
            io.tool_output(
                f"Plan updated{lang_hint}. Review the summary, then Yes / No / Change / View."
            )
        except Exception:
            pass
        # Compact re-confirm with revised approach/steps in subject
    # Exhausted rounds — last chance yes/no only
    io.tool_warning(
        "Change limit reached — Yes to proceed with the latest plan, No to abort."
    )
    ok = bool(
        io.confirm_ask(
            question,
            default="y",
            subject=format_plan_for_confirm(current),
        )
    )
    return ok, current
