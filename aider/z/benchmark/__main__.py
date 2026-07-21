"""CLI: ``python -m aider.z.benchmark`` or ``z benchmark``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="z benchmark",
        description="P2 software-engineering behavior benchmark",
    )
    sub = p.add_subparsers(dest="bench_command")

    run = sub.add_parser("run", help="Run the P2 benchmark suite")
    run.add_argument(
        "--ids",
        nargs="*",
        default=None,
        help="Optional issue id filter",
    )
    run.add_argument(
        "--no-baseline",
        action="store_true",
        help="Skip uncertainty-disabled baseline runs",
    )
    run.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Parallel workers (isolated checkouts)",
    )
    run.add_argument(
        "--results-dir",
        default=None,
        help="Where to write JSONL results (default: benchmarks/p2/results)",
    )
    run.add_argument(
        "--report",
        action="store_true",
        help="Print scoring report after the run",
    )

    score = sub.add_parser("score", help="Score a persisted run without re-executing")
    score.add_argument(
        "results_path",
        help="Path to run-*.jsonl from a prior benchmark run",
    )

    listing = sub.add_parser("list", help="List benchmark issues")
    listing.add_argument(
        "--by-type",
        action="store_true",
        help="Show counts per task type",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.bench_command or "run"

    if cmd == "list":
        from .issues import load_issues, summarize_task_type_counts

        issues = load_issues()
        if args.by_type:
            counts = summarize_task_type_counts(issues)
            for k in sorted(counts):
                print(f"{k}: {counts[k]}")
            print(f"total: {len(issues)}")
        else:
            for issue in issues:
                traps = ",".join(issue.known_traps) if issue.known_traps else "-"
                print(
                    f"{issue.id}\t{issue.task_type}\t{issue.fixture_repo}\ttraps={traps}"
                )
        return 0

    if cmd == "score":
        from .harness import load_results
        from .scoring import format_report, score_results

        results = load_results(Path(args.results_path))
        report = score_results(results)
        print(format_report(report))
        return 0

    # run
    from .harness import run_benchmark_suite
    from .scoring import format_report, score_results

    results = run_benchmark_suite(
        ids=args.ids,
        include_baseline=not args.no_baseline,
        parallel=max(1, int(args.parallel)),
        persist=True,
        results_dir=Path(args.results_dir) if args.results_dir else None,
    )
    print(f"Wrote {len(results)} result rows.")
    if args.report or True:
        report = score_results(results)
        print(format_report(report))
        # Also dump JSON aggregate beside results when possible
        if results:
            rid = results[0].run_id
            from .issues import default_benchmark_root

            out = Path(args.results_dir) if args.results_dir else (
                default_benchmark_root() / "results"
            )
            (out / f"run-{rid}.report.json").write_text(
                json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
