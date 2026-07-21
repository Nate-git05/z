"""InputOutput bridge for Z Editor — prompts become turn/waiting_input over IPC."""

from __future__ import annotations

import queue
import threading
import uuid
from typing import Any, Callable, Optional

from aider.io import InputOutput
from aider.z.turn_ux import TurnOrchestrator, TurnState, attach_orchestrator_to_io

NotifyFn = Callable[[str, dict], None]


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

    def _on_queue_change(self, n: int) -> None:
        turn_id = self._turn_id_provider()
        self._notify(
            "turn/queued",
            {"turnId": turn_id, "queueLen": n},
        )
        # Also refresh busy label
        orch = self.turn_orchestrator
        if orch and orch.state == TurnState.BUSY:
            self._on_state_change(orch.state, orch.phase)

    def llm_started(self) -> None:
        super().llm_started()
        try:
            orch = self.ensure_turn_ux()
            orch.enter_busy("Waiting for model…")
        except Exception:
            pass

    def emit_llm_delta(self, text: str) -> None:
        if not text:
            return
        self._notify(
            "item/agentMessage/delta",
            {"turnId": self._turn_id_provider(), "text": text},
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
