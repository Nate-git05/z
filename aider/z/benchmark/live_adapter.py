"""Live LLM / Z adapter for the P2 benchmark harness.

CI continues to use ``ScriptedAgentAdapter``. Enable live runs with::

    # Builtin Z coder (needs API keys + model)
    Z_P2_LIVE=1 Z_P2_LIVE_MODEL=gpt-4o-mini \\
      python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average \\
      --no-baseline

    # External hook (edits worktree, writes AgentTrace JSON)
    Z_P2_LIVE=1 Z_P2_LIVE_HOOK=scripts/p2_live_hook_example.py \\
      python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average

    # Offline replay (applies issue.scripted edits through the live pipeline)
    Z_P2_LIVE=1 Z_P2_LIVE_BACKEND=replay \\
      python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average

Backends (``Z_P2_LIVE_BACKEND`` or inferred):
- ``z`` / ``builtin`` — drive ``Coder`` against the worktree (default when live)
- ``hook`` — subprocess via ``Z_P2_LIVE_HOOK``
- ``replay`` — apply scripted/replay file edits without an LLM (tests / dry runs)
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent import AgentTrace, ScriptedAgentAdapter
from .issues import BenchmarkIssue
from .live_worktree import diff_worktree, seed_fnames_from_globs, snapshot_worktree


def live_adapter_enabled() -> bool:
    return os.environ.get("Z_P2_LIVE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def resolve_live_backend() -> str:
    """
    Pick backend: explicit ``Z_P2_LIVE_BACKEND``, else hook if set, else ``z``.
    """
    raw = (os.environ.get("Z_P2_LIVE_BACKEND") or "").strip().lower()
    if raw in ("z", "builtin", "coder", "aider"):
        return "z"
    if raw in ("hook", "script", "subprocess"):
        return "hook"
    if raw in ("replay", "scripted", "offline", "mock"):
        return "replay"
    if os.environ.get("Z_P2_LIVE_HOOK", "").strip():
        return "hook"
    return "z"


def live_model_name() -> str:
    return (
        os.environ.get("Z_P2_LIVE_MODEL")
        or os.environ.get("AIDER_MODEL")
        or os.environ.get("Z_MODEL")
        or "gpt-4o-mini"
    ).strip()


def live_max_turns() -> int:
    raw = os.environ.get("Z_P2_LIVE_MAX_TURNS", "").strip()
    if raw.isdigit():
        return max(1, min(8, int(raw)))
    return 3


class LiveAgentAdapter:
    """
    Opt-in live runner. Without ``Z_P2_LIVE=1``, returns a timed-out stub so
    accidental CI selection cannot spend tokens.
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
                    "Z_P2_LIVE_BACKEND=z|hook|replay (default z)."
                ),
            )

        backend = resolve_live_backend()
        if backend == "hook":
            return self._run_hook(issue, worktree, uncertainty_enabled=uncertainty_enabled, t0=t0)
        if backend == "replay":
            return self._run_replay(issue, worktree, uncertainty_enabled=uncertainty_enabled, t0=t0)
        return self._run_builtin_z(
            issue, worktree, uncertainty_enabled=uncertainty_enabled, t0=t0
        )

    # ------------------------------------------------------------------ hook

    def _run_hook(
        self,
        issue: BenchmarkIssue,
        worktree: Path,
        *,
        uncertainty_enabled: bool,
        t0: float,
    ) -> AgentTrace:
        hook = os.environ.get("Z_P2_LIVE_HOOK", "").strip()
        if not hook:
            return AgentTrace(
                timed_out=True,
                self_reported_complete=False,
                pipeline=["live_no_hook"],
                notes=(
                    "Backend=hook but Z_P2_LIVE_HOOK is unset. Point it at a "
                    "script that edits the worktree and writes AgentTrace JSON."
                ),
            )

        before = snapshot_worktree(worktree)
        trace_out = worktree / ".z_p2_live_trace.json"
        env = os.environ.copy()
        env["Z_P2_WORKTREE"] = str(worktree)
        env["Z_P2_PROMPT"] = issue.task_prompt
        env["Z_P2_ISSUE_ID"] = issue.id
        env["Z_P2_TRACE_OUT"] = str(trace_out)
        env["Z_P2_TASK_TYPE"] = issue.task_type
        env["Z_P2_UNCERTAINTY_ENABLED"] = "1" if uncertainty_enabled else "0"
        if uncertainty_enabled:
            env.pop("Z_UNCERTAINTY_DISABLED", None)
        else:
            env["Z_UNCERTAINTY_DISABLED"] = "1"

        timeout = float(issue.timeout_s or 300)
        try:
            cmd = (
                [hook]
                if os.path.isfile(hook) and os.access(hook, os.X_OK)
                else ["python3", hook]
            )
            proc = subprocess.run(
                cmd,
                cwd=str(worktree),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            edits = diff_worktree(before, worktree)
            return AgentTrace(
                edits=edits,
                timed_out=True,
                self_reported_complete=False,
                pipeline=["live_hook", "live_timeout"],
                notes=f"hook exceeded {timeout}s",
                time_to_first_edit=0.001 if edits else 0.0,
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

        # Prefer hook-reported edits; fall back to worktree diff.
        if not data.get("edits"):
            data["edits"] = diff_worktree(before, worktree)
        if "pipeline" not in data:
            data["pipeline"] = ["live_hook"]
        return _trace_from_dict(data, t0=t0)

    # ---------------------------------------------------------------- replay

    def _run_replay(
        self,
        issue: BenchmarkIssue,
        worktree: Path,
        *,
        uncertainty_enabled: bool,
        t0: float,
    ) -> AgentTrace:
        """
        Offline live-path exercise: apply authored/replay file edits without LLM.

        Used for CI plumbing tests and dry-runs of the live adapter surface.
        """
        meta = _classify_issue(issue)
        before = snapshot_worktree(worktree)
        edits: List[str] = []
        first_edit = 0.0
        root_cause = None
        findings: List[str] = []
        claim = False

        replay_path = os.environ.get("Z_P2_LIVE_REPLAY", "").strip()
        file_edits: Dict[str, str] = {}
        if replay_path and Path(replay_path).is_file():
            try:
                payload = json.loads(Path(replay_path).read_text(encoding="utf-8"))
                file_edits = dict(payload.get("file_edits") or {})
                root_cause = payload.get("root_cause_statement")
                findings = list(payload.get("review_findings") or [])
                claim = bool(payload.get("self_reported_complete", True))
            except (OSError, json.JSONDecodeError) as err:
                return AgentTrace(
                    timed_out=False,
                    self_reported_complete=False,
                    pipeline=["live_replay_error"],
                    notes=f"bad Z_P2_LIVE_REPLAY: {err}",
                    **meta,
                )
        elif issue.scripted and issue.scripted.file_edits:
            file_edits = dict(issue.scripted.file_edits)
            root_cause = issue.scripted.root_cause_statement
            findings = list(issue.scripted.review_findings or [])
            claim = bool(issue.scripted.claim_complete)
        elif issue.expected_edit_scope == "none":
            claim = True
            root_cause = (
                issue.scripted.root_cause_statement
                if issue.scripted
                else issue.ground_truth_root_cause
            )
            findings = list(issue.scripted.review_findings) if issue.scripted else []

        for rel, content in file_edits.items():
            path = worktree / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            edits.append(rel)
            if first_edit <= 0:
                first_edit = max(0.001, time.time() - t0)

        if not edits:
            edits = diff_worktree(before, worktree)
            if edits and first_edit <= 0:
                first_edit = max(0.001, time.time() - t0)

        return AgentTrace(
            edits=edits,
            mode=meta.get("mode"),
            implementation_plan_generated=bool(meta.get("implementation_plan_generated")),
            classified_clauses=list(meta.get("classified_clauses") or []),
            evidence_source_correct=None,
            root_cause_statement=root_cause,
            review_findings=findings,
            shell_commands=[],
            approval_interruptions=0,
            unnecessary_questions=0,
            time_to_first_edit=first_edit,
            self_reported_complete=claim,
            pipeline=["live_replay", f"uncertainty:{int(uncertainty_enabled)}"],
            notes="replay backend (no LLM)",
        )

    # ------------------------------------------------------------- builtin Z

    def _run_builtin_z(
        self,
        issue: BenchmarkIssue,
        worktree: Path,
        *,
        uncertainty_enabled: bool,
        t0: float,
    ) -> AgentTrace:
        meta = _classify_issue(issue)
        if not _has_model_credentials():
            return AgentTrace(
                timed_out=False,
                self_reported_complete=False,
                pipeline=["live_z", "live_no_credentials"],
                notes=(
                    "Builtin live backend needs an API key "
                    "(OPENAI_API_KEY / ANTHROPIC_API_KEY / …) "
                    "or use Z_P2_LIVE_BACKEND=replay|hook."
                ),
                mode=meta.get("mode"),
                classified_clauses=list(meta.get("classified_clauses") or []),
                implementation_plan_generated=bool(
                    meta.get("implementation_plan_generated")
                ),
            )

        before = snapshot_worktree(worktree)
        prev_unc = os.environ.get("Z_UNCERTAINTY_DISABLED")
        try:
            if uncertainty_enabled:
                os.environ.pop("Z_UNCERTAINTY_DISABLED", None)
            else:
                os.environ["Z_UNCERTAINTY_DISABLED"] = "1"

            return self._drive_coder(
                issue,
                worktree,
                before=before,
                meta=meta,
                uncertainty_enabled=uncertainty_enabled,
                t0=t0,
            )
        except Exception as err:  # noqa: BLE001 — surface as trace, don't crash suite
            return AgentTrace(
                edits=diff_worktree(before, worktree),
                timed_out=False,
                self_reported_complete=False,
                pipeline=["live_z", "live_z_error"],
                notes=f"{type(err).__name__}: {err}\n{traceback.format_exc()[-800:]}",
                mode=meta.get("mode"),
                classified_clauses=list(meta.get("classified_clauses") or []),
                implementation_plan_generated=bool(
                    meta.get("implementation_plan_generated")
                ),
            )
        finally:
            if prev_unc is None:
                os.environ.pop("Z_UNCERTAINTY_DISABLED", None)
            else:
                os.environ["Z_UNCERTAINTY_DISABLED"] = prev_unc

    def _drive_coder(
        self,
        issue: BenchmarkIssue,
        worktree: Path,
        *,
        before: Dict[str, str],
        meta: Dict[str, Any],
        uncertainty_enabled: bool,
        t0: float,
    ) -> AgentTrace:
        from aider.coders import Coder
        from aider.io import InputOutput
        from aider.models import Model
        from aider.z.uncertainty.done_gate import looks_like_done_claim

        model_name = live_model_name()
        io = InputOutput(yes=True, pretty=False, fancy_input=False)
        seed = seed_fnames_from_globs(worktree, issue.allowed_edit_globs)

        replies: List[str] = []
        first_edit = 0.0
        timed_out = False
        coder = None
        shell_commands: List[str] = []
        nodes_created = 0
        nodes_resolved = 0
        plan_approved = False
        task_mode_value = meta.get("mode")

        old_cwd = os.getcwd()
        try:
            os.chdir(str(worktree))
            main_model = Model(model_name)
            coder = Coder.create(
                main_model=main_model,
                io=io,
                fnames=seed or None,
                edit_format="diff",
                use_git=False,
                auto_commits=False,
            )
            coder.root = str(worktree)
            coder.repo = None

            max_turns = live_max_turns()
            timeout = float(issue.timeout_s or 300)
            deadline = t0 + timeout
            prompt = issue.task_prompt

            for turn in range(max_turns):
                if time.time() > deadline:
                    timed_out = True
                    break
                try:
                    followup = (
                        "Continue. If the task is done, say so briefly; "
                        "otherwise finish the remaining SEARCH/REPLACE edits."
                    )
                    coder.run(with_message=prompt if turn == 0 else followup)
                except KeyboardInterrupt:
                    timed_out = True
                    break
                except Exception as err:  # noqa: BLE001
                    replies.append(f"[turn {turn} error: {err}]")
                    break

                content = (getattr(coder, "partial_response_content", None) or "").strip()
                if content:
                    replies.append(content)
                edited_now = diff_worktree(before, worktree)
                if edited_now and first_edit <= 0:
                    first_edit = max(0.001, time.time() - t0)
                if content and looks_like_done_claim(content):
                    break
                if issue.expected_edit_scope == "none" and content:
                    break
                if edited_now and turn > 0 and not content:
                    break
                prompt = (
                    "Continue implementing the task. Prefer SEARCH/REPLACE on files "
                    "already in the chat."
                )

            shell_commands = list(getattr(coder, "shell_commands", None) or [])
            tm = getattr(coder, "task_mode", None)
            if tm is not None:
                task_mode_value = getattr(tm, "value", None) or task_mode_value
            eng = getattr(coder, "uncertainty_engine", None)
            if eng is not None:
                plan_approved = bool(
                    getattr(getattr(eng, "ctx", None), "plan_approved", False)
                )
                store = getattr(eng, "store", None)
                if store is not None:
                    try:
                        nodes = list(store.all() or [])
                        nodes_created = len(nodes)
                        nodes_resolved = sum(
                            1
                            for n in nodes
                            if str(
                                getattr(
                                    getattr(n, "status", None),
                                    "value",
                                    getattr(n, "status", ""),
                                )
                            ).lower()
                            == "resolved"
                        )
                    except Exception:
                        pass
        finally:
            os.chdir(old_cwd)

        edits = diff_worktree(before, worktree)
        joined = "\n".join(replies)
        claim = bool(joined and looks_like_done_claim(joined))
        if issue.expected_edit_scope == "none" and joined:
            claim = True

        root_cause = None
        findings: List[str] = []
        if issue.task_type in ("diagnosis", "review") and joined:
            para = next((p.strip() for p in joined.split("\n\n") if p.strip()), "")
            if issue.task_type == "diagnosis":
                root_cause = para[:500] or None
            else:
                findings = [para[:500]] if para else []

        return AgentTrace(
            edits=edits,
            mode=task_mode_value,
            implementation_plan_generated=bool(
                meta.get("implementation_plan_generated") or plan_approved
            ),
            classified_clauses=list(meta.get("classified_clauses") or []),
            evidence_source_correct=None,
            root_cause_statement=root_cause,
            review_findings=findings,
            shell_commands=shell_commands,
            approval_interruptions=0,
            unnecessary_questions=0,
            time_to_first_edit=first_edit,
            self_reported_complete=claim,
            uncertainty_nodes_created=nodes_created,
            uncertainty_nodes_resolved=nodes_resolved,
            timed_out=timed_out,
            pipeline=[
                "live_z",
                f"model:{model_name}",
                f"uncertainty:{int(uncertainty_enabled)}",
                f"turns:{len(replies)}",
            ],
            notes=(joined[:1000] if joined else "no model reply"),
        )


def _classify_issue(issue: BenchmarkIssue) -> Dict[str, Any]:
    """Free P0 classifiers shared across live backends."""
    from aider.z.task_mode import classify_task_mode
    from aider.z.uncertainty.clause import extract_clauses
    from aider.z.uncertainty.intent import extract_intent
    from aider.z.uncertainty.plan import draft_plan_from_request

    prompt = issue.task_prompt
    intent = extract_intent(prompt)
    mode = classify_task_mode(None, prompt, intent_mode=intent.mode)
    clauses = extract_clauses(prompt)
    clause_dicts = [{"kind": c.kind, "text": c.text} for c in clauses]
    plan_generated = False
    if mode.allows_planning:
        plan = draft_plan_from_request(prompt, intent=intent, reason="p2-live")
        plan_generated = not getattr(plan, "skipped", False)
    return {
        "mode": mode.value,
        "classified_clauses": clause_dicts,
        "implementation_plan_generated": plan_generated,
    }


def _has_model_credentials() -> bool:
    keys = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "AZURE_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GROQ_API_KEY",
    )
    return any(os.environ.get(k, "").strip() for k in keys)


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
    if key in ("live", "llm", "hook", "z"):
        return LiveAgentAdapter()
    return ScriptedAgentAdapter()
