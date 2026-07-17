"""Back-compat shim — concurrency is one row of the dynamic-risk taxonomy.

Prefer ``aider.z.uncertainty.dynamic_analysis`` for new code. This module keeps
the original concurrency-only API used by tests and older call sites.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from aider.run_cmd import run_cmd

from . import dynamic_analysis as _dynamic_analysis
from .dynamic_analysis import (
    DYNAMIC_RISK_CATEGORIES,
    DynamicComparison,
    DynamicRiskTag,
    SanitizerRunResult,
    SanitizerTool,
    analyze_category,
    category_by_id,
    classify_outcome,
    discover_tools_for_category,
    nodes_from_comparison,
    parse_issue_count,
    tag_category,
    tag_dynamic_risks,
)

# Re-export names expected by older imports / tests
RaceTool = SanitizerTool
RaceRunResult = SanitizerRunResult
RaceComparison = DynamicComparison
ConcurrencyTag = DynamicRiskTag


def _bridge_patched_symbols() -> None:
    """So unittest.mock.patch on this module reaches the real implementation."""
    _dynamic_analysis.run_cmd = run_cmd
    _dynamic_analysis.shutil = shutil


def tag_concurrency_relevant(
    diff: str = "",
    edited: Sequence[str] = (),
) -> DynamicRiskTag:
    cat = category_by_id("concurrency")
    assert cat is not None
    return tag_category(cat, diff, edited)


def parse_race_count(output: str) -> int:
    cat = category_by_id("concurrency")
    assert cat is not None
    return parse_issue_count(output, cat)


def discover_race_tools(
    root: Path,
    edited: Sequence[str] = (),
) -> List[SanitizerTool]:
    _bridge_patched_symbols()
    cat = category_by_id("concurrency")
    assert cat is not None
    return discover_tools_for_category(root, cat, edited)


def classify_race_outcome(
    before: Optional[SanitizerRunResult],
    after: Optional[SanitizerRunResult],
) -> Tuple[str, str]:
    return classify_outcome(before, after, issue_noun="race")


def concurrency_nodes_from_comparison(
    comparison: DynamicComparison,
    *,
    signals,
    files: Sequence[str] = (),
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
):
    return nodes_from_comparison(
        comparison,
        signals=signals,
        files=files,
        task_id=task_id,
        task_title=task_title,
        created_by_session=created_by_session,
        created_by_user=created_by_user,
    )


def analyze_concurrency_change(
    root: Path,
    *,
    diff: str = "",
    edited: Sequence[str] = (),
    verbose: bool = False,
    error_print=None,
    skip_before: bool = False,
) -> DynamicComparison:
    _bridge_patched_symbols()
    cat = category_by_id("concurrency")
    assert cat is not None
    return analyze_category(
        root,
        cat,
        diff=diff,
        edited=edited,
        verbose=verbose,
        error_print=error_print,
        skip_before=skip_before,
    )


__all__ = [
    "DYNAMIC_RISK_CATEGORIES",
    "RaceTool",
    "RaceRunResult",
    "RaceComparison",
    "ConcurrencyTag",
    "tag_concurrency_relevant",
    "parse_race_count",
    "discover_race_tools",
    "classify_race_outcome",
    "concurrency_nodes_from_comparison",
    "analyze_concurrency_change",
    "tag_dynamic_risks",
]
