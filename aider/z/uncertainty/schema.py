"""Uncertainty tree — schema, tiers, and node types.

Confidence and risk are separate, derived from concrete signals — never
model self-rated percentages.

Fail closed (see evidence_strategy.py): Fully Addressed requires a registered
kind's checkable predicate. Unknown kinds and model self-reports cannot raise
status above mechanical evidence — do not grow the system only by enumerating
new detectors after each miss.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


class NodeType(str, enum.Enum):
    """Human-worry node types (display values). Enum names kept stable for code."""

    # "I haven’t tested this path thoroughly"
    MISSING_TEST = "Untested Path"
    # "This might break on weird data"
    EDGE_CASE = "Edge Case Blind Spot"
    # "I’m assuming this API/lib behaves as I think"
    API_ASSUMPTION = "Unverified Assumption"
    # "Money/auth/data loss — be paranoid" (+ migrations)
    HIGH_STAKES = "High-Stakes Surface"
    MIGRATION_RISK = "Migration Risk"
    # "Looks right but feels clever/brittle"
    FRAGILE_LOGIC = "Fragile Logic"
    # "I copied a pattern; not sure it fits"
    PATTERN_INCONSISTENCY = "Pattern Misfit"
    # Kept for mature repos only; suppressed in greenfield
    NEW_FILE_NO_PATTERN = "New File (No Pattern Match)"
    # "Side effects I haven’t thought about"
    SHARED_LOGIC = "Integration Ripple"
    # "Didn’t check what happens if this fails" / secrets unverifiable
    FAILURE_BLIND_SPOT = "Failure Blind Spot"
    TODO_COMMENT = "TODO / Unclear Comment"
    UNVERIFIABLE_CONFIG = "Unverifiable Config"
    REQUIREMENT_GAP = "Requirement Gap"
    # Positive signal — informational only
    HIGH_CONFIDENCE = "Evidence of Safety"
    # Agent wrote a local package that shadows a real third-party dependency
    DEPENDENCY_FABRICATION = "Dependency Fabrication"
    # Broad except absorbs unexpected failures (freezegun-class limp-forward)
    ABSORBED_FAILURE = "Absorbed Failure"
    # Mutating the diff's new lines still leaves tests green
    WEAK_TEST = "Weak Test Suite"
    # Constructor/config params accepted without visible validation
    UNVALIDATED_CONFIG = "Unvalidated Config"
    # getattr(x, "new_param", default) papers over a param just introduced in this diff
    GETATTR_SHORTCUT = "Permissive getattr Shortcut"


# Older persisted display strings → current NodeType
_NODE_TYPE_ALIASES = {
    "Edge Case": NodeType.EDGE_CASE,
    "API Assumption": NodeType.API_ASSUMPTION,
    "Missing Test": NodeType.MISSING_TEST,
    "Pattern Inconsistency": NodeType.PATTERN_INCONSISTENCY,
    "Shared Logic / Blast Radius": NodeType.SHARED_LOGIC,
    "High Confidence": NodeType.HIGH_CONFIDENCE,
    "High-Stakes Surface": NodeType.HIGH_STAKES,
    "Fragile Logic": NodeType.FRAGILE_LOGIC,
    "Failure Blind Spot": NodeType.FAILURE_BLIND_SPOT,
    "Untested Path": NodeType.MISSING_TEST,
    "Edge Case Blind Spot": NodeType.EDGE_CASE,
    "Unverified Assumption": NodeType.API_ASSUMPTION,
    "Pattern Misfit": NodeType.PATTERN_INCONSISTENCY,
    "Integration Ripple": NodeType.SHARED_LOGIC,
    "Evidence of Safety": NodeType.HIGH_CONFIDENCE,
    "Dependency Fabrication": NodeType.DEPENDENCY_FABRICATION,
    "Environment Tampering": NodeType.DEPENDENCY_FABRICATION,
    "Absorbed Failure": NodeType.ABSORBED_FAILURE,
    "Weak Test Suite": NodeType.WEAK_TEST,
    "Unvalidated Config": NodeType.UNVALIDATED_CONFIG,
    "Permissive getattr Shortcut": NodeType.GETATTR_SHORTCUT,
}


def parse_node_type(value: str) -> NodeType:
    if value in NodeType._value2member_map_:
        return NodeType(value)
    if value in _NODE_TYPE_ALIASES:
        return _NODE_TYPE_ALIASES[value]
    # Fall back to enum name
    try:
        return NodeType[value]
    except KeyError as err:
        raise ValueError(f"Unknown node type: {value}") from err


class Tier(str, enum.Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class NodeStatus(str, enum.Enum):
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    RESOLVED = "Resolved"
    IGNORED = "Ignored"
    NEEDS_HUMAN_REVIEW = "Needs Human Review"
    BLOCKED = "Blocked"


class Area(str, enum.Enum):
    FRONTEND = "Frontend"
    BACKEND = "Backend"
    DATABASE = "Database"
    TESTS = "Tests"
    CONFIG = "Config"
    OTHER = "Other"


TIER_RANK = {Tier.HIGH: 0, Tier.MEDIUM: 1, Tier.LOW: 2}


@dataclass
class UncertaintyNode:
    title: str
    type: NodeType
    confidence_tier: Tier
    risk_tier: Tier
    summary: str
    explanation: str = ""
    files_affected: list[str] = field(default_factory=list)
    symbols_affected: list[str] = field(default_factory=list)
    why_uncertain: str = ""
    what_could_go_wrong: str = ""
    suggested_fix: str = ""
    suggested_tests: list[str] = field(default_factory=list)
    suggested_prompt: str = ""
    status: NodeStatus = NodeStatus.OPEN
    area: Area = Area.OTHER
    task_id: str | None = None
    task_title: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    resolved_at: str | None = None
    created_by_session: str | None = None
    created_by_user: str | None = None
    # Detector metadata (threshold, reference counts, checklist item, etc.)
    signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["confidence_tier"] = self.confidence_tier.value
        d["risk_tier"] = self.risk_tier.value
        d["status"] = self.status.value
        d["area"] = self.area.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UncertaintyNode:
        return cls(
            id=data.get("id") or str(uuid.uuid4()),
            title=data["title"],
            type=parse_node_type(data["type"]),
            confidence_tier=Tier(data["confidence_tier"]),
            risk_tier=Tier(data["risk_tier"]),
            summary=data.get("summary") or "",
            explanation=data.get("explanation") or "",
            files_affected=list(data.get("files_affected") or []),
            symbols_affected=list(data.get("symbols_affected") or []),
            why_uncertain=data.get("why_uncertain") or "",
            what_could_go_wrong=data.get("what_could_go_wrong") or "",
            suggested_fix=data.get("suggested_fix") or "",
            suggested_tests=list(data.get("suggested_tests") or []),
            suggested_prompt=data.get("suggested_prompt") or "",
            status=NodeStatus(data.get("status") or NodeStatus.OPEN.value),
            area=Area(data.get("area") or Area.OTHER.value),
            task_id=data.get("task_id"),
            task_title=data.get("task_title"),
            created_at=data.get("created_at")
            or datetime.now(timezone.utc).isoformat(),
            resolved_at=data.get("resolved_at"),
            created_by_session=data.get("created_by_session"),
            created_by_user=data.get("created_by_user"),
            signals=dict(data.get("signals") or {}),
        )


@dataclass
class RequirementItem:
    text: str
    status: str = "Not Addressed"  # Fully Addressed / Partially Addressed / Not Addressed
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # product | process | verification | decision | documentation | quality | external_assumption
    kind: str = "product"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status,
            "kind": self.kind,
        }


@dataclass
class TaskChecklist:
    task_id: str
    title: str
    items: list[RequirementItem] = field(default_factory=list)
    confirmed_by_user: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "confirmed_by_user": self.confirmed_by_user,
            "items": [i.to_dict() for i in self.items],
        }


# Keyword / path signals for high-stakes and migration detectors
HIGH_STAKES_KEYWORDS = {
    "billing",
    "payment",
    "stripe",
    "paypal",
    "checkout",
    "invoice",
    "auth",
    "oauth",
    "jwt",
    "password",
    "session",
    "security",
    "crypto",
    "encrypt",
    "permission",
    "rbac",
    "webhook",
    "secret",
    "credential",
}

MIGRATION_KEYWORDS = {
    "migration",
    "migrate",
    "alembic",
    "schema",
    "django.db.migrations",
    "create_table",
    "alter_table",
    "drop_table",
}

CONFIG_ENV_PATTERNS = (
    "os.environ",
    "os.getenv",
    "getenv(",
    "process.env",
    "SECRET",
    "API_KEY",
    "DATABASE_URL",
    "AWS_",
    "TWILIO_",
)

TODO_MARKERS = ("TODO", "FIXME", "XXX", "HACK", "BUG")

# Blast-radius default threshold (overridable via Z_BLAST_RADIUS_THRESHOLD)
DEFAULT_BLAST_RADIUS_THRESHOLD = 5

FRONTEND_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".vue", ".svelte", ".css", ".scss", ".html"}
BACKEND_HINTS = {"api", "server", "service", "router", "controller", "handler", "view"}
TEST_HINTS = {"test_", "_test", "tests/", "/test/", "spec.", ".spec."}


def infer_area(files: list[str]) -> Area:
    joined = " ".join(files).lower()
    if any(h in joined for h in TEST_HINTS) or any(
        "test" in PathLike(f).name.lower() for f in files
    ):
        return Area.TESTS
    if any(k in joined for k in MIGRATION_KEYWORDS) or "migration" in joined:
        return Area.DATABASE
    if any(PathLike(f).suffix.lower() in FRONTEND_SUFFIXES for f in files):
        return Area.FRONTEND
    if any(h in joined for h in BACKEND_HINTS):
        return Area.BACKEND
    if any(x in joined for x in (".env", "config", "settings", "docker", "yaml", "yml")):
        return Area.CONFIG
    return Area.OTHER


class PathLike:
    def __init__(self, path: str):
        self.path = path.replace("\\", "/")
        self.name = self.path.rsplit("/", 1)[-1]
        if "." in self.name:
            self.suffix = "." + self.name.rsplit(".", 1)[-1]
        else:
            self.suffix = ""


def text_looks_high_stakes(text: str) -> bool:
    lower = (text or "").lower()
    return any(k in lower for k in HIGH_STAKES_KEYWORDS)


def path_looks_high_stakes(path: str) -> bool:
    return text_looks_high_stakes(path.replace("\\", "/"))


def path_looks_migration(path: str) -> bool:
    lower = path.replace("\\", "/").lower()
    return any(k in lower for k in MIGRATION_KEYWORDS)


def text_looks_migration(text: str) -> bool:
    lower = (text or "").lower()
    return any(k in lower for k in MIGRATION_KEYWORDS)
