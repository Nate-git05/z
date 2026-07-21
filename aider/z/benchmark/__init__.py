"""P2 software-engineering behavior benchmark — package root."""

from .issues import BenchmarkIssue, load_issues
from .harness import BenchmarkResult, run_benchmark_issue, run_benchmark_suite
from .scoring import score_results, format_report

__all__ = [
    "BenchmarkIssue",
    "BenchmarkResult",
    "load_issues",
    "run_benchmark_issue",
    "run_benchmark_suite",
    "score_results",
    "format_report",
]
