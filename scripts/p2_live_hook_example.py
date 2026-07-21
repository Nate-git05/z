#!/usr/bin/env python3
"""Example Z_P2_LIVE_HOOK — apply a minimal fix and write AgentTrace JSON.

Usage::

    Z_P2_LIVE=1 Z_P2_LIVE_HOOK=scripts/p2_live_hook_example.py \\
      python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average \\
      --no-baseline

Env provided by the live adapter:
  Z_P2_WORKTREE, Z_P2_PROMPT, Z_P2_ISSUE_ID, Z_P2_TRACE_OUT,
  Z_P2_UNCERTAINTY_ENABLED, Z_P2_TASK_TYPE
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def main() -> int:
    worktree = Path(os.environ["Z_P2_WORKTREE"])
    prompt = os.environ.get("Z_P2_PROMPT", "")
    issue_id = os.environ.get("Z_P2_ISSUE_ID", "")
    trace_out = Path(os.environ["Z_P2_TRACE_OUT"])

    edits = []
    notes = f"example hook for {issue_id}"

    # Demo: fix the classic calc average off-by-one when that file exists.
    ops = worktree / "calcpkg" / "ops.py"
    if ops.is_file() and "average" in prompt.lower():
        text = ops.read_text(encoding="utf-8")
        fixed = re.sub(
            r"return sum\(nums\) / \(len\(nums\) - 1\)",
            "return sum(nums) / len(nums)",
            text,
        )
        if fixed != text:
            ops.write_text(fixed, encoding="utf-8")
            edits.append("calcpkg/ops.py")
            notes = "patched average off-by-one via example hook"

    trace = {
        "edits": edits,
        "self_reported_complete": bool(edits) or "diagnos" in (os.environ.get("Z_P2_TASK_TYPE") or ""),
        "root_cause_statement": (
            "average divides by len(nums)-1 off-by-one when length > 1"
            if edits
            else None
        ),
        "pipeline": ["live_hook_example"],
        "notes": notes,
        "mode": "implement",
    }
    trace_out.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
