"""P2.1 — BenchmarkIssue schema and loader."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence

TaskType = Literal[
    "diagnosis",
    "review",
    "bugfix",
    "feature",
    "migration",
    "refactor",
]
EditScope = Literal["none", "narrow", "broad"]
ClauseKind = Literal[
    "requested_action",
    "observation",
    "constraint",
    "acceptance_criterion",
    "process_rule",
    "investigation_target",
]


@dataclass
class IntendedClause:
    """Author-recorded intended clause breakdown for P1.1 precision/recall."""

    kind: ClauseKind
    text: str
    evidence_source: Optional[str] = None  # session | code | finding


@dataclass
class ScriptedSolution:
    """
    Deterministic agent actions for CI / unattended runs.

    Live LLM runs ignore this and use a LiveAgentAdapter instead.
    """

    file_edits: Dict[str, str] = field(default_factory=dict)  # relpath -> full contents
    root_cause_statement: Optional[str] = None
    review_findings: List[str] = field(default_factory=list)
    shell_commands: List[str] = field(default_factory=list)
    create_uncertainty_node: bool = False
    resolve_uncertainty_node: bool = False
    claim_complete: bool = True
    clarifying_questions: int = 0


@dataclass
class BenchmarkIssue:
    id: str
    task_type: TaskType
    fixture_repo: str  # relative to benchmarks/p2/fixtures/
    task_prompt: str
    hidden_tests: List[str]  # paths relative to fixture root, excluded during run
    expected_edit_scope: EditScope
    ground_truth_root_cause: Optional[str] = None
    known_traps: List[str] = field(default_factory=list)
    intended_clauses: List[IntendedClause] = field(default_factory=list)
    allowed_edit_globs: List[str] = field(default_factory=list)
    scripted: Optional[ScriptedSolution] = None
    timeout_s: float = 120.0
    version: str = "1"
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkIssue":
        clauses = [
            IntendedClause(**c) if isinstance(c, dict) else c
            for c in (data.get("intended_clauses") or [])
        ]
        scripted = data.get("scripted")
        if isinstance(scripted, dict):
            scripted = ScriptedSolution(**scripted)
        return cls(
            id=str(data["id"]),
            task_type=data["task_type"],
            fixture_repo=str(data["fixture_repo"]),
            task_prompt=str(data["task_prompt"]),
            hidden_tests=list(data.get("hidden_tests") or []),
            expected_edit_scope=data.get("expected_edit_scope") or "none",
            ground_truth_root_cause=data.get("ground_truth_root_cause"),
            known_traps=list(data.get("known_traps") or []),
            intended_clauses=clauses,
            allowed_edit_globs=list(data.get("allowed_edit_globs") or []),
            scripted=scripted,
            timeout_s=float(data.get("timeout_s") or 120.0),
            version=str(data.get("version") or "1"),
            notes=str(data.get("notes") or ""),
        )


def default_benchmark_root() -> Path:
    """Repo-relative benchmarks/p2 directory."""
    # aider/z/benchmark/issues.py → repo root is parents[3]
    here = Path(__file__).resolve()
    return here.parents[3] / "benchmarks" / "p2"


def load_issues(
    issues_dir: Optional[Path] = None,
    *,
    ids: Optional[Sequence[str]] = None,
) -> List[BenchmarkIssue]:
    """Load all ``*.json`` issue definitions from the P2 issues directory."""
    root = issues_dir or (default_benchmark_root() / "issues")
    root = Path(root)
    if not root.is_dir():
        return []

    issues: List[BenchmarkIssue] = []
    for path in sorted(root.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        issue = BenchmarkIssue.from_dict(data)
        if ids is not None and issue.id not in ids:
            continue
        issues.append(issue)
    return issues


def summarize_task_type_counts(issues: Sequence[BenchmarkIssue]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for issue in issues:
        counts[issue.task_type] = counts.get(issue.task_type, 0) + 1
    return counts
