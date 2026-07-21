"""Evidence records — every check stores provenance; edits invalidate stale evidence.

For every verification command, store:
  Exact command, working directory, exit code, relevant output,
  whether files changed afterward, commit/tree hash, environment
  assumptions, timestamp, and whether the evidence is now stale.

Any code edit after verification invalidates affected evidence.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def tree_hash(root: Path, *, paths: Sequence[str] = ()) -> str:
    """
    Stable content hash of the working tree (or a path subset).

    Used to detect staleness: if the hash changes after a record was written,
    that evidence is no longer valid for completion claims.
    """
    root = Path(root)
    h = hashlib.sha256()
    if paths:
        rels = sorted({str(p).replace("\\", "/").lstrip("./") for p in paths if p})
    else:
        rels = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip heavy/irrelevant dirs
            dirnames[:] = [
                d
                for d in dirnames
                if d
                not in {
                    ".git",
                    "node_modules",
                    ".venv",
                    "venv",
                    "__pycache__",
                    "dist",
                    "build",
                    ".next",
                    ".turbo",
                    "coverage",
                    ".tox",
                    ".mypy_cache",
                    ".pytest_cache",
                }
            ]
            for name in sorted(filenames):
                if name.endswith((".pyc", ".pyo", ".map")):
                    continue
                full = Path(dirpath) / name
                try:
                    rel = str(full.relative_to(root)).replace("\\", "/")
                except ValueError:
                    continue
                rels.append(rel)
                if len(rels) > 4000:
                    break
            if len(rels) > 4000:
                break
        rels = sorted(rels)

    for rel in rels:
        full = root / rel
        h.update(rel.encode("utf-8", errors="replace"))
        h.update(b"\0")
        try:
            if full.is_file():
                h.update(full.read_bytes()[:256_000])
        except OSError:
            h.update(b"missing")
        h.update(b"\n")
    return h.hexdigest()[:24]


@dataclass
class EvidenceRecord:
    """One checkable verification attempt with provenance."""

    kind: str  # typecheck | lint | unit | integration | build | start | smoke | e2e | clean_install
    command: str
    cwd: str
    exit_code: Optional[int] = None
    output_excerpt: str = ""
    passed: bool = False
    tree_hash_at_run: str = ""
    env_assumptions: List[str] = field(default_factory=list)
    files_touched_after: List[str] = field(default_factory=list)
    stale: bool = False
    timestamp: str = field(default_factory=_utcnow)
    evidence_type: str = ""  # multi_session_e2e | browser_e2e | unit_test | …
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceRecord":
        return cls(
            kind=str(data.get("kind") or ""),
            command=str(data.get("command") or ""),
            cwd=str(data.get("cwd") or ""),
            exit_code=data.get("exit_code"),
            output_excerpt=str(data.get("output_excerpt") or "")[-4000:],
            passed=bool(data.get("passed")),
            tree_hash_at_run=str(data.get("tree_hash_at_run") or ""),
            env_assumptions=list(data.get("env_assumptions") or []),
            files_touched_after=list(data.get("files_touched_after") or []),
            stale=bool(data.get("stale")),
            timestamp=str(data.get("timestamp") or _utcnow()),
            evidence_type=str(data.get("evidence_type") or ""),
            notes=str(data.get("notes") or ""),
        )


@dataclass
class EvidenceLedger:
    """Session ledger of evidence; edits mark affected records stale."""

    records: List[EvidenceRecord] = field(default_factory=list)
    last_tree_hash: str = ""

    def add(self, record: EvidenceRecord) -> EvidenceRecord:
        self.records.append(record)
        if record.tree_hash_at_run:
            self.last_tree_hash = record.tree_hash_at_run
        return record

    def invalidate_after_edits(
        self,
        *,
        current_tree_hash: str = "",
        edited: Sequence[str] = (),
    ) -> List[EvidenceRecord]:
        """Mark records stale when the tree hash moved or files changed after."""
        stale: List[EvidenceRecord] = []
        edited_set = {str(p).replace("\\", "/").lstrip("./") for p in edited if p}
        for rec in self.records:
            if rec.stale:
                continue
            if current_tree_hash and rec.tree_hash_at_run and current_tree_hash != rec.tree_hash_at_run:
                rec.stale = True
                rec.files_touched_after = sorted(edited_set)[:40]
                stale.append(rec)
            elif edited_set and rec.passed:
                # Any edit after a pass invalidates until re-run
                rec.stale = True
                rec.files_touched_after = sorted(edited_set)[:40]
                stale.append(rec)
        if current_tree_hash:
            self.last_tree_hash = current_tree_hash
        return stale

    def latest(self, kind: str) -> Optional[EvidenceRecord]:
        for rec in reversed(self.records):
            if rec.kind == kind:
                return rec
        return None

    def fresh_pass(self, kind: str) -> bool:
        rec = self.latest(kind)
        return bool(rec and rec.passed and not rec.stale)

    def to_dict(self) -> dict:
        return {
            "records": [r.to_dict() for r in self.records],
            "last_tree_hash": self.last_tree_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceLedger":
        ledger = cls(last_tree_hash=str(data.get("last_tree_hash") or ""))
        for raw in data.get("records") or []:
            if isinstance(raw, dict):
                ledger.records.append(EvidenceRecord.from_dict(raw))
        return ledger


def summarize_ledger(ledger: EvidenceLedger) -> str:
    if not ledger.records:
        return "Evidence ledger: (empty)"
    lines = ["Evidence ledger:"]
    for r in ledger.records[-12:]:
        mark = "✓" if r.passed and not r.stale else ("∅ stale" if r.stale else "✗")
        lines.append(
            f"  [{mark}] {r.kind}: exit={r.exit_code} hash={r.tree_hash_at_run or '-'} "
            f"cmd={r.command[:60]}"
        )
    return "\n".join(lines)
