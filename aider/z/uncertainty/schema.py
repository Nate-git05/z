"""Uncertainty tree — schema, tiers, and node types.

Confidence and risk are separate, derived from concrete signals — never
model self-rated percentages.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


class NodeType(str, enum.Enum):
    EDGE_CASE = "Edge Case"
    API_ASSUMPTION = "API Assumption"
    MISSING_TEST = "Missing Test"
    MIGRATION_RISK = "Migration Risk"
    PATTERN_INCONSISTENCY = "Pattern Inconsistency"
    NEW_FILE_NO_PATTERN = "New File (No Pattern Match)"
    SHARED_LOGIC = "Shared Logic / Blast Radius"
    TODO_COMMENT = "TODO / Unclear Comment"
    UNVERIFIABLE_CONFIG = "Unverifiable Config"
    REQUIREMENT_GAP = "Requirement Gap"
    HIGH_CONFIDENCE = "High Confidence"


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
            type=NodeType(data["type"]),
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
            "items": [
                {"id": i.id, "text": i.text, "status": i.status} for i in self.items
            ],
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
