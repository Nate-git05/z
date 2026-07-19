"""Per-(model, category) reliability tracking — anonymized metadata only."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()

# Explicit allowlist of persisted field names — privacy regression target.
ROUTING_OUTCOME_FIELDS = frozenset(
    {
        "model_id",
        "task_category",
        "gate_passed",
        "escalated",
        "checker_triggered",
        "cost_usd",
        "customer_id",
        "recorded_at",
    }
)


@dataclass
class RoutingOutcomeRecord:
    """Anonymized routing metadata — never request_text / diff / file contents."""

    model_id: str
    task_category: str  # CapabilityTier value, not the task text
    gate_passed: bool
    escalated: bool
    checker_triggered: Optional[str] = None
    cost_usd: float = 0.0
    customer_id: str = ""  # opaque scoping id only
    recorded_at: str = ""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def calibration_path() -> Path:
    base = Path(os.environ.get("Z_HOME", Path.home() / ".z"))
    d = base / "routing"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d / "calibration.json"


class CalibrationStore:
    """Dogfooded pass/fail history for reliability-adjusted selection."""

    def __init__(self, *, path: Optional[Path] = None, customer_id: str = "") -> None:
        self.path = path
        self.customer_id = customer_id
        self._records: List[RoutingOutcomeRecord] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self.path or calibration_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for row in data.get("records") or []:
            if not isinstance(row, dict):
                continue
            # Drop any unexpected free-text keys on load
            clean = {k: v for k, v in row.items() if k in ROUTING_OUTCOME_FIELDS}
            try:
                self._records.append(RoutingOutcomeRecord(**clean))
            except TypeError:
                continue

    def save(self) -> None:
        path = self.path or calibration_path()
        with _LOCK:
            payload = {
                "records": [asdict(r) for r in self._records[-2000:]],
                "updated_at": _utcnow(),
            }
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass

    def record_outcome(
        self,
        model_id: str,
        task_category: str,
        gate_passed: bool,
        *,
        escalated: bool = False,
        checker_triggered: Optional[str] = None,
        cost_usd: float = 0.0,
        customer_id: Optional[str] = None,
    ) -> RoutingOutcomeRecord:
        self._ensure_loaded()
        rec = RoutingOutcomeRecord(
            model_id=model_id,
            task_category=task_category,
            gate_passed=gate_passed,
            escalated=escalated,
            checker_triggered=checker_triggered,
            cost_usd=cost_usd,
            customer_id=customer_id or self.customer_id,
            recorded_at=_utcnow(),
        )
        # Privacy: refuse to persist any extra attrs smuggled onto the instance
        for name in list(vars(rec)):
            if name not in ROUTING_OUTCOME_FIELDS:
                delattr(rec, name)
        self._records.append(rec)
        self.save()
        return rec

    def _recent(
        self,
        model_id: str,
        task_category: str,
        *,
        window_days: int = 30,
    ) -> List[RoutingOutcomeRecord]:
        self._ensure_loaded()
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        out: List[RoutingOutcomeRecord] = []
        for r in self._records:
            if r.model_id != model_id or r.task_category != task_category:
                continue
            if self.customer_id and r.customer_id and r.customer_id != self.customer_id:
                continue
            try:
                ts = datetime.fromisoformat(r.recorded_at.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                out.append(r)
        return out

    def reliability_penalty(self, model_id: str, task_category: str) -> float:
        """0.0 cold start; rises toward 1.0 as gate-failure rate increases."""
        records = self._recent(model_id, task_category, window_days=30)
        if len(records) < 10:
            return 0.0
        fail_rate = sum(1 for r in records if not r.gate_passed) / len(records)
        return min(fail_rate * 1.5, 1.0)

    @staticmethod
    def assert_record_is_metadata_only(rec: RoutingOutcomeRecord) -> None:
        """Raise AssertionError if a record carries disallowed free-text fields."""
        names = {f.name for f in fields(rec)}
        extra = names - ROUTING_OUTCOME_FIELDS
        if extra:
            raise AssertionError(f"disallowed routing record fields: {sorted(extra)}")
        for banned in (
            "request_text",
            "diff",
            "file_contents",
            "prompt",
            "completion",
            "code",
        ):
            if hasattr(rec, banned):
                raise AssertionError(f"routing record must not include {banned}")
