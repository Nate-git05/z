"""InputOutput bridge for Z Editor — prompts become turn/waiting_input over IPC."""

from __future__ import annotations

import queue
import re
import threading
import uuid
from typing import Any, Callable, Optional

from aider.io import InputOutput
from aider.z.app_server.activity import TurnActivityTracker, map_phase_id
from aider.z.app_server.turn_trace import TurnTraceTracker
from aider.z.turn_ux import TurnOrchestrator, TurnState, attach_orchestrator_to_io

NotifyFn = Callable[[str, dict], None]


def _strip_reasoning_markers(text: str) -> str:
    """Remove THINKING/ANSWER chrome and raw reasoning tags from answer deltas."""
    if not text:
        return ""
    try:
        from aider.reasoning_tags import REASONING_END, REASONING_START, REASONING_TAG

        out = str(text)
        out = out.replace(REASONING_START, "").replace(REASONING_END, "")
        out = re.sub(
            rf"<{re.escape(REASONING_TAG)}>.*?</{re.escape(REASONING_TAG)}>",
            "",
            out,
            flags=re.DOTALL,
        )
        out = re.sub(rf"</?{re.escape(REASONING_TAG)}>", "", out)
        return out
    except Exception:
        return str(text)


class AppServerIO(InputOutput):
    """Headless IO: no TTY; approvals wait on ``turn/respond`` from the editor."""

    def __init__(
        self,
        *,
        notify: NotifyFn,
        turn_id_provider: Callable[[], Optional[str]],
        root: str = ".",
    ):
        # Parent __init__ may call tool_output before our attrs exist.
        self._notify = notify
        self._turn_id_provider = turn_id_provider
        self._response_q: queue.Queue = queue.Queue()
        self._pending_request_id: Optional[str] = None
        self._cancel = threading.Event()
        self.turn_orchestrator: Optional[TurnOrchestrator] = None
        self.activity = TurnActivityTracker(notify, turn_id_provider)
        self.trace = TurnTraceTracker(notify, turn_id_provider)
        super().__init__(
            pretty=False,
            fancy_input=False,
            yes=None,
            z_theme=True,
            root=root,
            dry_run=False,
        )

    # --- lifecycle ---------------------------------------------------------

    def set_cancelled(self) -> None:
        self._cancel.set()
        # Unblock any waiter
        try:
            self._response_q.put_nowait({"__cancelled__": True})
        except Exception:
            pass

    def clear_cancelled(self) -> None:
        self._cancel.clear()

    def deliver_response(
        self,
        request_id: str,
        response: Any = None,
        *,
        text: Optional[str] = None,
    ) -> bool:
        if not self._pending_request_id or request_id != self._pending_request_id:
            return False
        item: dict[str, Any] = {"requestId": request_id, "response": response}
        if text is not None:
            item["text"] = text
        self._response_q.put(item)
        return True

    def ensure_turn_ux(self):
        if self.turn_orchestrator is not None:
            return self.turn_orchestrator
        orch = TurnOrchestrator(
            on_state_change=self._on_state_change,
            on_queue_change=self._on_queue_change,
        )
        attach_orchestrator_to_io(self, orch)
        return orch

    def _on_state_change(self, state: TurnState, phase: Optional[str]) -> None:
        turn_id = self._turn_id_provider()
        orch = self.turn_orchestrator
        params: dict[str, Any] = {
            "turnId": turn_id,
            "state": state.value if isinstance(state, TurnState) else str(state),
            "phase": phase,
            "queueLen": orch.queue_len if orch else 0,
            "waitingKind": orch.waiting_kind if orch else None,
            "label": orch.status_label(phase) if orch else phase,
        }
        self._notify("turn/busy", params)
        try:
            st = state.value if isinstance(state, TurnState) else str(state)
            if st == "idle":
                self.activity.set_phase("idle")
            elif st == "waiting_input":
                self.activity.set_phase("waiting")
            else:
                label = params.get("label") or phase
                self.activity.set_phase(map_phase_id(str(label or phase or "")) or "thinking")
            self.activity.flush(force=True)
        except Exception:
            pass

    def _on_queue_change(self, n: int) -> None:
        turn_id = self._turn_id_provider()
        orch = self.turn_orchestrator
        items: list[str] = []
        preview = None
        if orch is not None:
            try:
                items = list(orch.list_queue())
            except Exception:
                items = []
            if items:
                try:
                    preview = orch.format_queued_preview(items[0])
                except Exception:
                    one = " ".join(items[0].split())
                    preview = f"▶ queued: {one[:71]}…" if len(one) > 72 else f"▶ queued: {one}"
        self._notify(
            "turn/queued",
            {
                "turnId": turn_id,
                "queueLen": n,
                "items": items,
                "preview": preview,
            },
        )
        # Also refresh busy label
        if orch and orch.state == TurnState.BUSY:
            self._on_state_change(orch.state, orch.phase)

    def llm_started(self) -> None:
        super().llm_started()
        try:
            orch = self.ensure_turn_ux()
            orch.enter_busy("Waiting for model…")
        except Exception:
            pass
        try:
            self.activity.set_phase("thinking")
            self.activity.flush(force=True)
        except Exception:
            pass
        try:
            self.trace.open_thinking()
        except Exception:
            pass

    def emit_llm_reasoning_delta(self, text: str) -> None:
        """Buffer model reasoning for turn traces — never into the answer bubble."""
        if not text:
            return
        try:
            self.trace.append_reasoning(text)
        except Exception:
            pass

    def emit_llm_delta(self, text: str) -> None:
        if not text:
            return
        cleaned = _strip_reasoning_markers(text)
        if not cleaned.strip():
            # Pure reasoning chrome — keep buffering if tagged leftovers arrived here.
            try:
                if text and ("THINKING" in text or "thinking-content-" in text):
                    self.trace.append_reasoning(text)
            except Exception:
                pass
            return
        try:
            self.trace.close_thinking_if_open()
        except Exception:
            pass
        self._notify(
            "item/agentMessage/delta",
            {"turnId": self._turn_id_provider(), "text": cleaned},
        )

    # --- output ------------------------------------------------------------

    def tool_output(self, *messages, log_only=False, bold=False, mirror_history=None):
        text = " ".join(str(m) for m in messages if m is not None)
        notify = getattr(self, "_notify", None)
        if text and not log_only and callable(notify):
            try:
                notify(
                    "turn/log",
                    {
                        "turnId": self._turn_id_provider()
                        if callable(getattr(self, "_turn_id_provider", None))
                        else None,
                        "level": "info",
                        "text": text,
                    },
                )
            except Exception:
                pass
        if text and not log_only:
            try:
                act = getattr(self, "activity", None)
                if act is not None:
                    act.observe_tool_output(text)
                    act.maybe_flush()
            except Exception:
                pass
            try:
                tr = getattr(self, "trace", None)
                if tr is not None:
                    tr.observe_tool_line(text)
            except Exception:
                pass
        return super().tool_output(
            *messages, log_only=True, bold=bold, mirror_history=mirror_history
        )

    def tool_warning(self, message="", strip=True):
        if message:
            self._notify(
                "turn/log",
                {
                    "turnId": self._turn_id_provider(),
                    "level": "warning",
                    "text": str(message),
                },
            )
        return super().tool_warning(message, strip=strip)

    def tool_error(self, message="", strip=True):
        if message:
            self._notify(
                "turn/log",
                {
                    "turnId": self._turn_id_provider(),
                    "level": "error",
                    "text": str(message),
                },
            )
        return super().tool_error(message, strip=strip)

    def assistant_output(self, message, pretty=None):
        if message:
            self.emit_llm_delta(str(message))
        # Avoid terminal markdown stream
        return None

    # --- prompts → waiting_input -------------------------------------------

    def confirm_ask(
        self,
        question,
        default="y",
        subject=None,
        explicit_yes_required=False,
        group=None,
        allow_never=False,
    ):
        with self._waiting_input_scope("confirm"):
            question_id = (question, subject)
            if question_id in self.never_prompts:
                return False
            options = ["yes", "no"]
            if group and not explicit_yes_required:
                options.append("all")
            if group:
                options.append("skip")
            if allow_never:
                options.append("don't")
            raw = self._await_user_input(
                kind="confirm",
                question=str(question),
                subject=subject if isinstance(subject, str) else None,
                default=default,
                options=options,
                explicit_yes_required=explicit_yes_required,
                allow_never=allow_never,
            )
            if raw is None or raw.get("__cancelled__"):
                return False
            answer = str(raw.get("response", "")).strip().lower() or default.lower()
            if answer.startswith("d") and allow_never:
                self.never_prompts.add(question_id)
                return False
            if group:
                if answer.startswith("a") and not explicit_yes_required:
                    group.preference = "all"
                elif answer.startswith("s"):
                    group.preference = "skip"
            if explicit_yes_required:
                return answer.startswith("y")
            return answer[:1] in ("y", "a")

    def plan_confirm_ask(self, question, *, subject=None, default="y"):
        with self._waiting_input_scope("plan_confirm"):
            raw = self._await_user_input(
                kind="plan_confirm",
                question=str(question),
                subject=subject if isinstance(subject, str) else None,
                default=default,
                options=["yes", "no", "change", "view"],
                explicit_yes_required=False,
                allow_never=False,
            )
            if raw is None or raw.get("__cancelled__"):
                return "no"
            answer = str(raw.get("response", "")).strip().lower()
            text = raw.get("text")
            if text and isinstance(text, str) and text.strip():
                # Free-text revision from Chat UI
                self._pending_plan_change = text.strip()
                return "change"
            if not answer:
                answer = default.lower()
            if answer.startswith("c"):
                return "change"
            if answer.startswith("v"):
                return "view"
            if answer.startswith("y"):
                return "yes"
            return "no"

    def prompt_ask(self, question, default="", subject=None):
        with self._waiting_input_scope("prompt"):
            raw = self._await_user_input(
                kind="prompt",
                question=str(question),
                subject=subject if isinstance(subject, str) else None,
                default=default or "",
                options=None,
                explicit_yes_required=False,
                allow_never=False,
            )
            if raw is None or raw.get("__cancelled__"):
                return default or ""
            if "text" in raw and raw["text"] is not None:
                return str(raw["text"])
            resp = raw.get("response")
            if resp is None:
                return default or ""
            return str(resp)

    def confirm_mcp_first_use(
        self,
        server_name: str,
        tool_name: str = "*",
        *,
        forever: bool = True,
    ) -> bool:
        """
        D9 first-use gate for MCP tools.

        If already confirmed in ``~/.z/mcp/first_use.json``, return True.
        Otherwise emit ``turn/waiting_input`` kind ``mcp_tool`` and persist
        approval on yes.
        """
        from aider.z import mcp_local

        if not mcp_local.needs_first_use_confirm(server_name, tool_name):
            return True
        with self._waiting_input_scope("mcp_tool"):
            question = (
                f"Allow MCP tool `{tool_name}` from server `{server_name}`?"
                if tool_name and tool_name != "*"
                else f"Allow MCP tools from server `{server_name}`?"
            )
            raw = self._await_user_input(
                kind="mcp_tool",
                question=question,
                subject=f"{server_name}::{tool_name}",
                default="n",
                options=["yes", "no"],
                explicit_yes_required=True,
                allow_never=False,
            )
            if raw is None or raw.get("__cancelled__"):
                return False
            answer = str(raw.get("response", "")).strip().lower()
            if not answer.startswith("y"):
                return False
            mcp_local.mark_first_use_confirmed(
                server_name, tool_name, forever=forever
            )
            return True

    def _await_user_input(
        self,
        *,
        kind: str,
        question: str,
        subject: Optional[str],
        default: str,
        options: Optional[list],
        explicit_yes_required: bool,
        allow_never: bool,
    ) -> Optional[dict]:
        if self._cancel.is_set():
            return {"__cancelled__": True}
        request_id = str(uuid.uuid4())
        self._pending_request_id = request_id
        # Drain stale responses
        while True:
            try:
                self._response_q.get_nowait()
            except queue.Empty:
                break
        self._notify(
            "turn/waiting_input",
            {
                "turnId": self._turn_id_provider(),
                "requestId": request_id,
                "kind": kind,
                "question": question,
                "subject": subject,
                "default": default,
                "options": options,
                "explicitYesRequired": explicit_yes_required,
                "allowNever": allow_never,
            },
        )
        try:
            self.trace.mark_waiting(kind=str(kind or ""), question=str(question or ""))
        except Exception:
            pass
        while True:
            if self._cancel.is_set():
                self._pending_request_id = None
                return {"__cancelled__": True}
            try:
                item = self._response_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item.get("__cancelled__"):
                self._pending_request_id = None
                return item
            if item.get("requestId") != request_id:
                continue
            self._pending_request_id = None
            return item
