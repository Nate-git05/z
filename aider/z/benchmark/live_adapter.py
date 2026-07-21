"""Live LLM adapter skeleton for the P2 benchmark harness.

CI continues to use ``ScriptedAgentAdapter``. Enable live runs with::

    Z_P2_LIVE=1 Z_P2_LIVE_HOOK=/path/to/hook.py python -m aider.z.benchmark run \\
        --adapter live --ids p2-011-bugfix-average

The hook receives env ``Z_P2_WORKTREE``, ``Z_P2_PROMPT``, ``Z_P2_ISSUE_ID``,
``Z_P2_TRACE_OUT`` and must write a JSON file matching ``AgentTrace`` fields
(at minimum ``edits``, ``self_reported_complete``). The hook should edit the
worktree in place; listed ``edits`` are paths relative to the worktree.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .agent import AgentTrace, ScriptedAgentAdapter
from .issues import BenchmarkIssue


def live_adapter_enabled() -> bool:
    return os.environ.get("Z_P2_LIVE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class LiveAgentAdapter:
    """
    Opt-in live runner. Without ``Z_P2_LIVE=1``, returns a failed/timeout trace
    so accidental CI selection cannot spend tokens.
    """

    def run(
        self,
        issue: BenchmarkIssue,
        worktree: Path,
        *,
        uncertainty_enabled: bool,
    ) -> AgentTrace:
        t0 = time.time()
        if not live_adapter_enabled():
            return AgentTrace(
                timed_out=True,
                self_reported_complete=False,
                pipeline=["live_disabled"],
                notes=(
                    "Live adapter disabled. Set Z_P2_LIVE=1 and optionally "
                    "Z_P2_LIVE_HOOK to a script that writes a JSON AgentTrace."
                ),
            )

        hook = os.environ.get("Z_P2_LIVE_HOOK", "").strip()
        if not hook:
            return AgentTrace(
                timed_out=True,
                self_reported_complete=False,
                pipeline=["live_no_hook"],
                notes=(
                    "Z_P2_LIVE=1 but Z_P2_LIVE_HOOK is unset. Point it at a "
                    "script that runs your model against the worktree."
                ),
            )

        trace_out = worktree / ".z_p2_live_trace.json"
        env = os.environ.copy()
        env["Z_P2_WORKTREE"] = str(worktree)
        env["Z_P2_PROMPT"] = issue.task_prompt
        env["Z_P2_ISSUE_ID"] = issue.id
        env["Z_P2_TRACE_OUT"] = str(trace_out)
        env["Z_P2_UNCERTAINTY_ENABLED"] = "1" if uncertainty_enabled else "0"
        if uncertainty_enabled:
            env.pop("Z_UNCERTAINTY_DISABLED", None)
        else:
            env["Z_UNCERTAINTY_DISABLED"] = "1"

        timeout = float(issue.timeout_s or 300)
        try:
            cmd = [hook] if os.path.isfile(hook) and os.access(hook, os.X_OK) else [
                "python3",
                hook,
            ]
            proc = subprocess.run(
                cmd,
                cwd=str(worktree),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return AgentTrace(
                timed_out=True,
                self_reported_complete=False,
                pipeline=["live_timeout"],
                notes=f"hook exceeded {timeout}s",
                time_to_first_edit=0.0,
            )
        except OSError as err:
            return AgentTrace(
                timed_out=False,
                self_reported_complete=False,
                pipeline=["live_hook_error"],
                notes=str(err),
            )

        data: Dict[str, Any] = {}
        if trace_out.is_file():
            try:
                data = json.loads(trace_out.read_text(encoding="utf-8"))
            except json.JSONDecodeError as err:
                data = {"notes": f"invalid trace JSON: {err}"}
        else:
            data = {
                "notes": (
                    f"hook exit={proc.returncode}; no trace file. "
                    f"stderr={(proc.stderr or '')[:500]}"
                ),
                "self_reported_complete": False,
            }

        return _trace_from_dict(data, t0=t0)


def _trace_from_dict(data: Dict[str, Any], *, t0: float) -> AgentTrace:
    edits = list(data.get("edits") or [])
    first_edit = float(data.get("time_to_first_edit") or 0.0)
    if edits and first_edit <= 0:
        first_edit = max(0.001, time.time() - t0)
    return AgentTrace(
        edits=edits,
        mode=data.get("mode"),
        implementation_plan_generated=bool(data.get("implementation_plan_generated")),
        classified_clauses=list(data.get("classified_clauses") or []),
        evidence_source_correct=data.get("evidence_source_correct"),
        root_cause_statement=data.get("root_cause_statement"),
        review_findings=list(data.get("review_findings") or []),
        shell_commands=list(data.get("shell_commands") or []),
        approval_interruptions=int(data.get("approval_interruptions") or 0),
        unnecessary_questions=int(data.get("unnecessary_questions") or 0),
        time_to_first_edit=first_edit,
        time_blocked_on_approval_or_sync=float(
            data.get("time_blocked_on_approval_or_sync") or 0.0
        ),
        self_reported_complete=bool(data.get("self_reported_complete")),
        verification_command_changed_after_failure=bool(
            data.get("verification_command_changed_after_failure")
        ),
        uncertainty_nodes_created=int(data.get("uncertainty_nodes_created") or 0),
        uncertainty_nodes_resolved=int(data.get("uncertainty_nodes_resolved") or 0),
        timed_out=bool(data.get("timed_out")),
        pipeline=list(data.get("pipeline") or ["live_hook"]),
        notes=str(data.get("notes") or ""),
    )


def select_adapter(name: Optional[str] = None):
    """Factory used by the CLI / harness."""
    key = (name or os.environ.get("Z_P2_ADAPTER") or "scripted").strip().lower()
    if key in ("live", "llm", "hook"):
        return LiveAgentAdapter()
    return ScriptedAgentAdapter()
