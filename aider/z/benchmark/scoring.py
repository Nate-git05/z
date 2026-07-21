"""P2.3 — Scoring and reporting over persisted BenchmarkResult records."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .harness import BenchmarkResult
from .issues import BenchmarkIssue, load_issues


@dataclass
class MetricSet:
    issue_resolution_rate: float = 0.0
    hidden_test_pass_rate: float = 0.0
    false_completion_rate: float = 0.0
    unnecessary_edit_rate: float = 0.0
    avg_unnecessary_edits: float = 0.0
    unnecessary_planning_rate: float = 0.0
    requirement_classification_precision: Optional[float] = None
    requirement_classification_recall: Optional[float] = None
    correct_evidence_source_rate: Optional[float] = None
    avg_approval_interruptions: float = 0.0
    median_time_to_first_edit: Optional[float] = None
    median_time_to_verified_completion: Optional[float] = None
    avg_time_blocked_on_approval_or_sync: float = 0.0
    timeout_rate: float = 0.0
    n_issues: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoreReport:
    full: MetricSet = field(default_factory=MetricSet)
    baseline: MetricSet = field(default_factory=MetricSet)
    delta: Dict[str, Optional[float]] = field(default_factory=dict)
    by_task_type: Dict[str, Dict[str, MetricSet]] = field(default_factory=dict)
    flagged_issues: List[Dict[str, Any]] = field(default_factory=list)
    tradeoff_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "full": self.full.to_dict(),
            "baseline": self.baseline.to_dict(),
            "delta": self.delta,
            "by_task_type": {
                tt: {k: v.to_dict() for k, v in sides.items()}
                for tt, sides in self.by_task_type.items()
            },
            "flagged_issues": self.flagged_issues,
            "tradeoff_summary": self.tradeoff_summary,
        }


def _safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def _median(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    return float(statistics.median(vals))


def _clause_pr(
    results: Sequence[BenchmarkResult],
    issues_by_id: Dict[str, BenchmarkIssue],
) -> tuple[Optional[float], Optional[float]]:
    """Precision/recall of classified clause kinds vs author-intended kinds."""
    tp = fp = fn = 0
    any_intended = False
    for r in results:
        issue = issues_by_id.get(r.issue_id)
        if not issue or not issue.intended_clauses:
            continue
        any_intended = True
        intended_kinds = {c.kind for c in issue.intended_clauses}
        got_kinds = {c.get("kind") for c in r.classified_clauses if c.get("kind")}
        tp += len(intended_kinds & got_kinds)
        fp += len(got_kinds - intended_kinds)
        fn += len(intended_kinds - got_kinds)
    if not any_intended:
        return None, None
    prec = _safe_div(tp, tp + fp)
    rec = _safe_div(tp, tp + fn)
    return prec, rec


def compute_metrics(
    results: Sequence[BenchmarkResult],
    *,
    issues: Optional[Sequence[BenchmarkIssue]] = None,
    task_type_filter: Optional[str] = None,
) -> MetricSet:
    issues_by_id = {i.id: i for i in (issues or load_issues())}
    filtered: List[BenchmarkResult] = []
    for r in results:
        if task_type_filter:
            issue = issues_by_id.get(r.issue_id)
            if not issue or issue.task_type != task_type_filter:
                continue
        filtered.append(r)

    n = len(filtered)
    if n == 0:
        return MetricSet()

    resolved = sum(1 for r in filtered if r.actually_complete)
    false_complete = sum(
        1 for r in filtered if r.self_reported_complete and not r.actually_complete
    )
    unneeded_issues = sum(1 for r in filtered if r.unnecessary_edits)
    unneeded_counts = [len(r.unnecessary_edits) for r in filtered]

    # Hidden tests across all issues
    ht_pass = ht_total = 0
    for r in filtered:
        for ok in r.hidden_test_details.values():
            ht_total += 1
            if ok:
                ht_pass += 1

    # Unnecessary planning: diagnosis/review with a plan
    diag_review = []
    for r in filtered:
        issue = issues_by_id.get(r.issue_id)
        if issue and issue.task_type in ("diagnosis", "review"):
            diag_review.append(r)
    plan_bad = sum(1 for r in diag_review if r.implementation_plan_generated)

    prec, rec = _clause_pr(filtered, issues_by_id)

    evidence_vals = [
        r.evidence_source_correct
        for r in filtered
        if r.evidence_source_correct is not None
    ]
    evidence_rate = (
        _safe_div(sum(1 for v in evidence_vals if v), len(evidence_vals))
        if evidence_vals
        else None
    )

    first_edits = [r.time_to_first_edit for r in filtered if r.time_to_first_edit > 0]
    verified = [r.time_to_verified_completion for r in filtered]
    blocked = [r.time_blocked_on_approval_or_sync for r in filtered]
    timeouts = sum(1 for r in filtered if r.timed_out)

    return MetricSet(
        issue_resolution_rate=_safe_div(resolved, n),
        hidden_test_pass_rate=_safe_div(ht_pass, ht_total) if ht_total else (
            _safe_div(resolved, n)
        ),
        false_completion_rate=_safe_div(false_complete, n),
        unnecessary_edit_rate=_safe_div(unneeded_issues, n),
        avg_unnecessary_edits=_safe_div(sum(unneeded_counts), n),
        unnecessary_planning_rate=_safe_div(plan_bad, len(diag_review)),
        requirement_classification_precision=prec,
        requirement_classification_recall=rec,
        correct_evidence_source_rate=evidence_rate,
        avg_approval_interruptions=_safe_div(
            sum(r.approval_interruptions for r in filtered), n
        ),
        median_time_to_first_edit=_median(first_edits),
        median_time_to_verified_completion=_median(verified),
        avg_time_blocked_on_approval_or_sync=_safe_div(sum(blocked), n),
        timeout_rate=_safe_div(timeouts, n),
        n_issues=n,
    )


def _delta(full: MetricSet, baseline: MetricSet) -> Dict[str, Optional[float]]:
    keys = [
        "issue_resolution_rate",
        "hidden_test_pass_rate",
        "false_completion_rate",
        "unnecessary_edit_rate",
        "unnecessary_planning_rate",
        "avg_approval_interruptions",
        "avg_time_blocked_on_approval_or_sync",
        "timeout_rate",
    ]
    out: Dict[str, Optional[float]] = {}
    for k in keys:
        fv = getattr(full, k)
        bv = getattr(baseline, k)
        if fv is None or bv is None:
            out[k] = None
        else:
            out[k] = float(fv) - float(bv)
    return out


def _tradeoff(full: MetricSet, baseline: MetricSet, delta: Dict[str, Optional[float]]) -> str:
    res_d = delta.get("issue_resolution_rate") or 0.0
    fc_d = delta.get("false_completion_rate") or 0.0
    ap_d = delta.get("avg_approval_interruptions") or 0.0
    blk_d = delta.get("avg_time_blocked_on_approval_or_sync") or 0.0
    lines = [
        "Uncertainty/planning layer trade-off (full − baseline):",
        f"  resolution rate Δ = {res_d:+.3f}  (higher is better)",
        f"  false completion Δ = {fc_d:+.3f}  (lower/more-negative is better)",
        f"  approval interruptions Δ = {ap_d:+.3f}  (lower/more-negative is better)",
        f"  time blocked Δ = {blk_d:+.3f}s  (lower/more-negative is better)",
    ]
    justified = res_d > 0.05 and fc_d < -0.05 and ap_d <= 0.5
    if justified:
        lines.append(
            "  Verdict: layer is net-positive on resolution/false-completion "
            "without a large interruption cost."
        )
    else:
        lines.append(
            "  Verdict: inspect per-task-type breakdown — aggregate may hide "
            "regressions, or gains may not yet clear the interruption cost."
        )
    # Sample-size caveats
    if full.n_issues < 10:
        lines.append(
            f"  Caveat: n={full.n_issues} is small; treat type-level swings cautiously."
        )
    return "\n".join(lines)


def _flagged(
    results: Sequence[BenchmarkResult],
    issues_by_id: Dict[str, BenchmarkIssue],
) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    for r in results:
        if not r.uncertainty_enabled:
            continue
        reasons = []
        if r.timed_out:
            reasons.append("timeout")
        if r.self_reported_complete and not r.actually_complete:
            reasons.append("false_completion")
        if r.approval_interruptions >= 3:
            reasons.append("high_approvals")
        if r.time_blocked_on_approval_or_sync > 1.0:
            reasons.append("blocked_time")
        if r.unnecessary_edits:
            reasons.append("unnecessary_edits")
        if reasons:
            issue = issues_by_id.get(r.issue_id)
            flags.append(
                {
                    "issue_id": r.issue_id,
                    "task_type": issue.task_type if issue else None,
                    "reasons": reasons,
                    "approval_interruptions": r.approval_interruptions,
                    "actually_complete": r.actually_complete,
                }
            )
    return flags


def score_results(
    results: Sequence[BenchmarkResult],
    *,
    issues: Optional[Sequence[BenchmarkIssue]] = None,
) -> ScoreReport:
    issue_list = list(issues) if issues is not None else load_issues()
    issues_by_id = {i.id: i for i in issue_list}

    full_rows = [r for r in results if r.uncertainty_enabled]
    base_rows = [r for r in results if not r.uncertainty_enabled]

    full = compute_metrics(full_rows, issues=issue_list)
    baseline = compute_metrics(base_rows, issues=issue_list)
    delta = _delta(full, baseline)

    by_type: Dict[str, Dict[str, MetricSet]] = {}
    types = sorted({i.task_type for i in issue_list})
    for tt in types:
        by_type[tt] = {
            "full": compute_metrics(full_rows, issues=issue_list, task_type_filter=tt),
            "baseline": compute_metrics(
                base_rows, issues=issue_list, task_type_filter=tt
            ),
        }

    return ScoreReport(
        full=full,
        baseline=baseline,
        delta=delta,
        by_task_type=by_type,
        flagged_issues=_flagged(results, issues_by_id),
        tradeoff_summary=_tradeoff(full, baseline, delta),
    )


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "  n/a "
    return f"{100.0 * x:5.1f}%"


def _fmt_num(x: Optional[float], digits: int = 3) -> str:
    if x is None:
        return "  n/a "
    return f"{x:.{digits}f}"


def format_report(report: ScoreReport) -> str:
    lines: List[str] = []
    lines.append("Z P2 Software-Engineering Behavior Benchmark")
    lines.append("=" * 52)
    lines.append("")
    lines.append(
        f"{'metric':<42} {'full':>8} {'baseline':>8} {'Δ':>8}"
    )
    lines.append("-" * 70)

    rows = [
        ("issue_resolution_rate", True),
        ("hidden_test_pass_rate", True),
        ("false_completion_rate", True),
        ("unnecessary_edit_rate", True),
        ("unnecessary_planning_rate", True),
        ("avg_approval_interruptions", False),
        ("avg_time_blocked_on_approval_or_sync", False),
        ("timeout_rate", True),
        ("requirement_classification_precision", True),
        ("requirement_classification_recall", True),
        ("correct_evidence_source_rate", True),
        ("median_time_to_first_edit", False),
        ("median_time_to_verified_completion", False),
    ]
    for key, as_pct in rows:
        fv = getattr(report.full, key)
        bv = getattr(report.baseline, key)
        dv = report.delta.get(key)
        if dv is None and fv is not None and bv is not None:
            try:
                dv = float(fv) - float(bv)
            except (TypeError, ValueError):
                dv = None
        if as_pct:
            lines.append(
                f"{key:<42} {_fmt_pct(fv):>8} {_fmt_pct(bv):>8} "
                f"{(_fmt_pct(dv) if dv is not None else '  n/a '):>8}"
            )
        else:
            d_s = _fmt_num(dv) if dv is not None else "  n/a "
            lines.append(
                f"{key:<42} {_fmt_num(fv):>8} {_fmt_num(bv):>8} {d_s:>8}"
            )

    lines.append("")
    lines.append(f"n_issues (full)={report.full.n_issues}  "
                 f"(baseline)={report.baseline.n_issues}")
    lines.append("")
    lines.append("Per task type (resolution / false-completion):")
    lines.append("-" * 52)
    for tt, sides in sorted(report.by_task_type.items()):
        f = sides["full"]
        b = sides["baseline"]
        caveat = "  [small-n]" if f.n_issues < 5 else ""
        lines.append(
            f"  {tt:<12} full res={_fmt_pct(f.issue_resolution_rate)} "
            f"fc={_fmt_pct(f.false_completion_rate)} "
            f"| base res={_fmt_pct(b.issue_resolution_rate)} "
            f"fc={_fmt_pct(b.false_completion_rate)} "
            f"n={f.n_issues}{caveat}"
        )

    # Surface type-level regressions vs aggregate improvement
    lines.append("")
    agg_res = report.delta.get("issue_resolution_rate") or 0.0
    if agg_res > 0:
        for tt, sides in sorted(report.by_task_type.items()):
            f = sides["full"].issue_resolution_rate
            b = sides["baseline"].issue_resolution_rate
            if f < b:
                lines.append(
                    f"  REGRESSION FLAG: {tt} resolution fell "
                    f"({_fmt_pct(b)} → {_fmt_pct(f)}) while aggregate rose."
                )

    lines.append("")
    lines.append(report.tradeoff_summary)
    lines.append("")
    if report.flagged_issues:
        lines.append("Flagged issues (full layer):")
        for fl in report.flagged_issues:
            lines.append(
                f"  - {fl['issue_id']} ({fl.get('task_type')}): "
                + ", ".join(fl["reasons"])
            )
    else:
        lines.append("Flagged issues (full layer): none")
    lines.append("")
    return "\n".join(lines)
