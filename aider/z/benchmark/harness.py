"""P2.2 — Benchmark harness: clean checkout, run agent, score tree, persist."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .agent import AgentAdapter, AgentTrace, ScriptedAgentAdapter, path_matches_globs
from .issues import BenchmarkIssue, default_benchmark_root, load_issues


@dataclass
class BenchmarkResult:
    issue_id: str
    uncertainty_enabled: bool
    edits: List[str] = field(default_factory=list)
    hidden_tests_passed: bool = False
    hidden_test_details: Dict[str, bool] = field(default_factory=dict)
    root_cause_match: Optional[bool] = None
    unnecessary_edits: List[str] = field(default_factory=list)
    unnecessary_questions: int = 0
    verification_command_changed_after_failure: bool = False
    approval_interruptions: int = 0
    time_to_first_edit: float = 0.0
    time_to_verified_completion: float = 0.0
    time_blocked_on_approval_or_sync: float = 0.0
    self_reported_complete: bool = False
    actually_complete: bool = False
    timed_out: bool = False
    implementation_plan_generated: bool = False
    mode: Optional[str] = None
    classified_clauses: List[Dict[str, Any]] = field(default_factory=list)
    evidence_source_correct: Optional[bool] = None
    uncertainty_nodes_created: int = 0
    uncertainty_nodes_resolved: int = 0
    run_id: str = ""
    started_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkResult":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def _copy_fixture(fixture_src: Path, dest: Path, exclude_hidden: Sequence[str]) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        fixture_src,
        dest,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    # Remove hidden tests so the agent cannot see/run them
    for rel in exclude_hidden:
        target = dest / rel
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)


def _restore_hidden_tests(fixture_src: Path, worktree: Path, hidden: Sequence[str]) -> None:
    for rel in hidden:
        src = fixture_src / rel
        dst = worktree / rel
        if not src.exists():
            continue
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _run_hidden_tests(worktree: Path, hidden: Sequence[str]) -> Dict[str, bool]:
    """
    Run each hidden test path with unittest.

    Files: ``python -m unittest <module>``
    Dirs:  ``python -m unittest discover -s <dir> -v``
    """
    details: Dict[str, bool] = {}
    if not hidden:
        return details

    env = os.environ.copy()
    env["PYTHONPATH"] = str(worktree) + os.pathsep + env.get("PYTHONPATH", "")

    for rel in hidden:
        target = worktree / rel
        if not target.exists():
            details[rel] = False
            continue
        if target.is_dir():
            cmd = [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(target),
                "-v",
            ]
        else:
            cmd = [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(target.parent),
                "-p",
                target.name,
                "-v",
            ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(worktree),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            details[rel] = proc.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            details[rel] = False
    return details


def _root_cause_match(issue: BenchmarkIssue, statement: Optional[str]) -> Optional[bool]:
    if issue.ground_truth_root_cause is None:
        return None
    if not statement:
        return False
    truth = issue.ground_truth_root_cause.lower()
    got = statement.lower()
    # Token overlap: require key phrases from ground truth appear in statement
    # Authors encode distinctive tokens; require >= 50% of significant words.
    tokens = [t for t in truth.replace(",", " ").split() if len(t) > 3]
    if not tokens:
        return truth in got or got in truth
    hits = sum(1 for t in tokens if t in got)
    return hits >= max(1, (len(tokens) + 1) // 2)


def _unnecessary_edits(issue: BenchmarkIssue, edits: List[str]) -> List[str]:
    if issue.expected_edit_scope == "none":
        return list(edits)
    if issue.expected_edit_scope == "narrow" and issue.allowed_edit_globs:
        return [e for e in edits if not path_matches_globs(e, issue.allowed_edit_globs)]
    # broad: only flag edits clearly outside the fixture package conventions
    return [e for e in edits if e.startswith("UNNECESSARY_") or e.endswith(".bak")]


def _actually_complete(
    issue: BenchmarkIssue,
    *,
    hidden_ok: bool,
    root_match: Optional[bool],
    edits: List[str],
) -> bool:
    if issue.task_type in ("diagnosis", "review"):
        # Must not edit; diagnosis scored on root-cause match, review on no-edits
        if edits:
            return False
        if issue.task_type == "diagnosis":
            return bool(root_match)
        return True
    # Edit tasks: hidden tests are ground truth
    if not issue.hidden_tests:
        return bool(root_match) if root_match is not None else False
    return hidden_ok and (root_match is not False)


def run_benchmark_issue(
    issue: BenchmarkIssue,
    *,
    uncertainty_enabled: bool = True,
    adapter: Optional[AgentAdapter] = None,
    fixtures_root: Optional[Path] = None,
    run_id: Optional[str] = None,
) -> BenchmarkResult:
    """
    Check out fixture clean, run agent, restore + run hidden tests, return result.
    """
    started = datetime.now(timezone.utc).isoformat()
    rid = run_id or uuid.uuid4().hex[:12]
    root = fixtures_root or (default_benchmark_root() / "fixtures")
    fixture_src = Path(root) / issue.fixture_repo
    if not fixture_src.is_dir():
        return BenchmarkResult(
            issue_id=issue.id,
            uncertainty_enabled=uncertainty_enabled,
            notes=f"missing fixture: {fixture_src}",
            run_id=rid,
            started_at=started,
            timed_out=False,
            actually_complete=False,
            self_reported_complete=False,
        )

    adapter = adapter or ScriptedAgentAdapter()
    t0 = time.time()

    with tempfile.TemporaryDirectory(prefix=f"z_p2_{issue.id}_") as tmp:
        worktree = Path(tmp) / "repo"
        _copy_fixture(fixture_src, worktree, issue.hidden_tests)

        # Scripted adapter takes uncertainty_enabled directly — do not mutate
        # process-global env (races under --parallel). Live adapters may set env.
        from .agent import ScriptedAgentAdapter as _SA

        set_env = not isinstance(adapter, _SA)
        prev = os.environ.get("Z_UNCERTAINTY_DISABLED") if set_env else None
        try:
            if set_env:
                if uncertainty_enabled:
                    os.environ.pop("Z_UNCERTAINTY_DISABLED", None)
                else:
                    os.environ["Z_UNCERTAINTY_DISABLED"] = "1"
            trace: AgentTrace = adapter.run(
                issue, worktree, uncertainty_enabled=uncertainty_enabled
            )
            timed_out = (time.time() - t0) > issue.timeout_s or trace.timed_out
        finally:
            if set_env:
                if prev is None:
                    os.environ.pop("Z_UNCERTAINTY_DISABLED", None)
                else:
                    os.environ["Z_UNCERTAINTY_DISABLED"] = prev

        _restore_hidden_tests(fixture_src, worktree, issue.hidden_tests)
        details = _run_hidden_tests(worktree, issue.hidden_tests)
        hidden_ok = all(details.values()) if details else (
            issue.task_type in ("diagnosis", "review")
        )

        root_match = _root_cause_match(issue, trace.root_cause_statement)
        unneeded = _unnecessary_edits(issue, trace.edits)
        complete = _actually_complete(
            issue,
            hidden_ok=hidden_ok if details else (
                bool(root_match) if issue.task_type == "diagnosis" else not bool(trace.edits)
            ),
            root_match=root_match,
            edits=trace.edits,
        )
        if timed_out:
            complete = False

        verified_t = time.time() - t0
        return BenchmarkResult(
            issue_id=issue.id,
            uncertainty_enabled=uncertainty_enabled,
            edits=list(trace.edits),
            hidden_tests_passed=hidden_ok if details else complete,
            hidden_test_details=details,
            root_cause_match=root_match,
            unnecessary_edits=unneeded,
            unnecessary_questions=trace.unnecessary_questions,
            verification_command_changed_after_failure=(
                trace.verification_command_changed_after_failure
            ),
            approval_interruptions=trace.approval_interruptions,
            time_to_first_edit=trace.time_to_first_edit,
            time_to_verified_completion=verified_t,
            time_blocked_on_approval_or_sync=trace.time_blocked_on_approval_or_sync,
            self_reported_complete=trace.self_reported_complete,
            actually_complete=complete,
            timed_out=timed_out,
            implementation_plan_generated=trace.implementation_plan_generated,
            mode=trace.mode,
            classified_clauses=list(trace.classified_clauses),
            evidence_source_correct=trace.evidence_source_correct,
            uncertainty_nodes_created=trace.uncertainty_nodes_created,
            uncertainty_nodes_resolved=trace.uncertainty_nodes_resolved,
            run_id=rid,
            started_at=started,
            notes=trace.notes,
        )


def persist_results(
    results: Sequence[BenchmarkResult],
    *,
    results_dir: Optional[Path] = None,
    run_id: Optional[str] = None,
) -> Path:
    out_dir = Path(results_dir or (default_benchmark_root() / "results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    rid = run_id or (results[0].run_id if results else uuid.uuid4().hex[:12])
    path = out_dir / f"run-{rid}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.to_dict()) + "\n")
    meta = {
        "run_id": rid,
        "n_results": len(results),
        "written_at": datetime.now(timezone.utc).isoformat(),
        "path": str(path),
    }
    (out_dir / f"run-{rid}.meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return path


def load_results(path: Path) -> List[BenchmarkResult]:
    results: List[BenchmarkResult] = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            results.append(BenchmarkResult.from_dict(json.loads(line)))
    return results


def run_benchmark_suite(
    *,
    issues: Optional[Sequence[BenchmarkIssue]] = None,
    issues_dir: Optional[Path] = None,
    ids: Optional[Sequence[str]] = None,
    include_baseline: bool = True,
    adapter: Optional[AgentAdapter] = None,
    parallel: int = 1,
    persist: bool = True,
    results_dir: Optional[Path] = None,
    run_id: Optional[str] = None,
) -> List[BenchmarkResult]:
    """
    Run all issues (full layer, and optionally baseline with uncertainty disabled).
    """
    issue_list = list(issues) if issues is not None else load_issues(issues_dir, ids=ids)
    rid = run_id or uuid.uuid4().hex[:12]
    jobs: List[tuple] = []
    for issue in issue_list:
        jobs.append((issue, True))
        if include_baseline:
            jobs.append((issue, False))

    results: List[BenchmarkResult] = []

    def _one(issue: BenchmarkIssue, enabled: bool) -> BenchmarkResult:
        return run_benchmark_issue(
            issue,
            uncertainty_enabled=enabled,
            adapter=adapter,
            run_id=rid,
        )

    if parallel <= 1:
        for issue, enabled in jobs:
            results.append(_one(issue, enabled))
    else:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futs = [pool.submit(_one, issue, enabled) for issue, enabled in jobs]
            for fut in as_completed(futs):
                results.append(fut.result())
        # Stable order: by issue id then full-before-baseline
        results.sort(key=lambda r: (r.issue_id, not r.uncertainty_enabled))

    if persist:
        persist_results(results, results_dir=results_dir, run_id=rid)
    return results
