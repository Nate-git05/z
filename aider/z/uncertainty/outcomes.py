"""Thin disposition telemetry for uncertainty detectors.

Tracks created / ignored / resolved / force_override / medium_ack per node type
so we can see which detectors get overridden most (boy-who-cried-wolf signal).

Does not auto-tune thresholds yet — just persist + report.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_LOCK = threading.Lock()

DISPOSITIONS = (
    "created",
    "ignored",
    "resolved",
    "force_override",
    "medium_ack",
)

# Cap retained event log (aggregates are unbounded per type but tiny)
_MAX_EVENTS = 500


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def outcomes_path() -> Path:
    base = Path(os.environ.get("Z_HOME", Path.home() / ".z"))
    d = base / "uncertainty"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d / "outcomes.json"


def _empty() -> dict:
    return {"by_detector": {}, "events": [], "updated_at": _utcnow()}


def load_outcomes() -> dict:
    path = outcomes_path()
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty()
        data.setdefault("by_detector", {})
        data.setdefault("events", [])
        return data
    except (OSError, json.JSONDecodeError):
        return _empty()


def save_outcomes(data: dict) -> None:
    path = outcomes_path()
    try:
        data["updated_at"] = _utcnow()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass


def _detector_key(node_type: Any) -> str:
    if node_type is None:
        return "Unknown"
    if hasattr(node_type, "value"):
        return str(node_type.value)
    return str(node_type)


def record_outcome(
    node_type: Any,
    disposition: str,
    *,
    repo_key: str = "",
    node_id: str = "",
    extra: Optional[dict] = None,
) -> None:
    """Increment aggregate counters and append a capped event row."""
    disposition = (disposition or "").strip().lower()
    if disposition not in DISPOSITIONS:
        return
    key = _detector_key(node_type)
    with _LOCK:
        data = load_outcomes()
        bucket = data["by_detector"].setdefault(key, {d: 0 for d in DISPOSITIONS})
        for d in DISPOSITIONS:
            bucket.setdefault(d, 0)
        bucket[disposition] = int(bucket.get(disposition) or 0) + 1

        event = {
            "at": _utcnow(),
            "detector": key,
            "disposition": disposition,
            "repo_key": repo_key or "",
            "node_id": node_id or "",
        }
        if extra:
            event["extra"] = extra
        events: List[dict] = list(data.get("events") or [])
        events.append(event)
        if len(events) > _MAX_EVENTS:
            events = events[-_MAX_EVENTS:]
        data["events"] = events
        save_outcomes(data)


def record_node_created(node, *, repo_key: str = "") -> None:
    record_outcome(
        getattr(node, "type", None),
        "created",
        repo_key=repo_key,
        node_id=getattr(node, "id", "") or "",
        extra={"edge_source": (getattr(node, "signals", None) or {}).get("edge_source")},
    )


def record_nodes_created(nodes: Sequence, *, repo_key: str = "") -> None:
    for n in nodes or []:
        record_node_created(n, repo_key=repo_key)


def override_rate(bucket: dict) -> float:
    """Share of dispositions that dismiss/override vs resolve — rough noise signal."""
    created = int(bucket.get("created") or 0)
    if created <= 0:
        return 0.0
    overrides = (
        int(bucket.get("ignored") or 0)
        + int(bucket.get("force_override") or 0)
        + int(bucket.get("medium_ack") or 0)
    )
    return overrides / created


def resolution_rate(bucket: dict) -> float:
    """resolved / created — 0% with high volume is a broken-detector signal."""
    created = int(bucket.get("created") or 0)
    if created <= 0:
        return 0.0
    return int(bucket.get("resolved") or 0) / created


# Circuit breaker defaults (Claude Fix 1.3): 0% resolution past threshold → noisy
_CIRCUIT_MIN_CREATED = 10
_CIRCUIT_MAX_RESOLUTION = 0.05


def detector_circuit_open(
    detector_key: str,
    *,
    min_created: int = _CIRCUIT_MIN_CREATED,
    max_resolution_rate: float = _CIRCUIT_MAX_RESOLUTION,
) -> bool:
    """
    True when a detector has enough history and almost never resolves.

    Used to downgrade severity / flag noise instead of blocking forever.
    """
    data = load_outcomes()
    bucket = (data.get("by_detector") or {}).get(detector_key) or {}
    created = int(bucket.get("created") or 0)
    if created < min_created:
        return False
    return resolution_rate(bucket) <= max_resolution_rate


def circuit_warnings(*, top: int = 10) -> List[str]:
    """Human-readable warnings for detectors with open noise circuits."""
    data = load_outcomes()
    warnings: List[str] = []
    for det, bucket in (data.get("by_detector") or {}).items():
        if detector_circuit_open(det):
            created = int(bucket.get("created") or 0)
            resolved = int(bucket.get("resolved") or 0)
            warnings.append(
                f"{det}: resolution {resolved}/{created} "
                f"({resolution_rate(bucket) * 100:.0f}%) — severity auto-downgraded"
            )
    return warnings[:top]


def format_stats(*, top: int = 20) -> str:
    """Human-readable disposition table for CLI / /uncertainties stats."""
    data = load_outcomes()
    by = data.get("by_detector") or {}
    if not by:
        return (
            "No uncertainty disposition data yet.\n"
            "Stats accumulate as nodes are created, ignored, resolved, "
            "or force-committed."
        )

    rows = []
    for det, bucket in by.items():
        created = int(bucket.get("created") or 0)
        rows.append(
            (
                override_rate(bucket),
                created,
                det,
                bucket,
            )
        )
    rows.sort(key=lambda r: (-r[0], -r[1], r[2]))

    lines = [
        "Uncertainty detector dispositions (local)",
        f"Updated: {data.get('updated_at') or '(unknown)'}",
        "",
        f"{'Detector':<28} {'creat':>5} {'ign':>4} {'res':>4} {'force':>5} {'ack':>4} {'ovr%':>5}",
        "-" * 62,
    ]
    for rate, created, det, bucket in rows[:top]:
        name = det if len(det) <= 28 else det[:25] + "…"
        lines.append(
            f"{name:<28} {created:5d} "
            f"{int(bucket.get('ignored') or 0):4d} "
            f"{int(bucket.get('resolved') or 0):4d} "
            f"{int(bucket.get('force_override') or 0):5d} "
            f"{int(bucket.get('medium_ack') or 0):4d} "
            f"{rate * 100:4.0f}%"
        )
    lines.append("")
    lines.append(
        "ovr% ≈ (ignored + force_override + medium_ack) / created — "
        "high values may mean that detector is noisy."
    )
    warns = circuit_warnings()
    if warns:
        lines.append("")
        lines.append("Noise circuit open (severity resolution ≈ 0% — severity downgraded):")
        for w in warns:
            lines.append(f"  • {w}")
    lines.append(f"Raw log: {outcomes_path()}")
    return "\n".join(lines)


def reset_outcomes() -> None:
    """Test helper — wipe the outcomes file."""
    path = outcomes_path()
    with _LOCK:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
