"""Durable cross-thread commit-block ledger for the Z Editor commit-block view.

Append-only JSONL under ``~/.z/commit_blocks/``. Written when the verify gate
blocks a commit; listed via z-app-server ``commit_blocks/list``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def ledger_dir() -> Path:
    base = Path(os.environ.get("Z_HOME", Path.home() / ".z"))
    path = base / "commit_blocks"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _safe_repo_key(repo_key: Optional[str]) -> str:
    key = (repo_key or "default").strip() or "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return (safe[:80] if safe else "default").rstrip("_") or "default"


def ledger_path(repo_key: Optional[str] = None) -> Path:
    return ledger_dir() / f"{_safe_repo_key(repo_key)}.jsonl"


def append_block(
    *,
    reason: str,
    repo_key: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    verify_state: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    record = {
        "id": str(uuid.uuid4()),
        "created_at": _utcnow(),
        "repo_key": repo_key or "default",
        "session_id": session_id,
        "thread_id": thread_id,
        "reason": (reason or "").strip(),
        "verify_state": verify_state,
        "state": "blocked",  # blocked | overridden | resolved
        "override_meta": None,
        "extra": dict(extra or {}),
    }
    path = ledger_path(repo_key)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def list_blocks(
    *,
    repo_key: Optional[str] = None,
    limit: int = 200,
) -> List[dict[str, Any]]:
    path = ledger_path(repo_key)
    if not path.is_file():
        return []
    rows: List[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rows.reverse()  # newest first
    return rows


def set_block_state(
    block_id: str,
    state: str,
    *,
    repo_key: Optional[str] = None,
    override_meta: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Rewrite ledger with updated state for one id (small files — fine for V1)."""
    path = ledger_path(repo_key)
    if not path.is_file():
        return None
    updated: Optional[dict[str, Any]] = None
    out_lines: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out_lines.append(line)
            continue
        if row.get("id") == block_id:
            row["state"] = state
            if override_meta is not None:
                row["override_meta"] = override_meta
            row["updated_at"] = _utcnow()
            updated = row
        out_lines.append(json.dumps(row, ensure_ascii=False))
    if updated is not None:
        path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return updated
