"""Background turn execution for z-app-server (Phase 4)."""

from __future__ import annotations

import logging
import os
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from aider.z.app_server.io_bridge import AppServerIO

logger = logging.getLogger("z.app_server.turns")

NotifyFn = Callable[[str, dict], None]


class ThreadTurnRunner:
    """One persistent Coder + worker queue per chat threadId."""

    def __init__(
        self,
        *,
        thread_id: str,
        workspace_root: str,
        notify: NotifyFn,
    ):
        self.thread_id = thread_id
        self.workspace_root = str(Path(workspace_root).resolve())
        self._notify = notify
        self._lock = threading.Lock()
        self._job_q: list[tuple[str, str]] = []  # (turn_id, text) waiting to start
        self._worker: Optional[threading.Thread] = None
        self._coder = None
        self._io: Optional[AppServerIO] = None
        self._current_turn_id: Optional[str] = None
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def current_turn_id(self) -> Optional[str]:
        return self._current_turn_id

    def _turn_id(self) -> Optional[str]:
        return self._current_turn_id

    def start_turn(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("turn/start requires text")

        with self._lock:
            # If a turn is running, try queue (Busy) or reject
            if self._busy and self._io is not None:
                orch = self._io.ensure_turn_ux()
                if orch.enqueue(text):
                    return {
                        "turnId": self._current_turn_id,
                        "threadId": self.thread_id,
                        "accepted": True,
                        "queued": True,
                        "queueLen": orch.queue_len,
                    }
                raise RuntimeError("Agent is busy and the turn queue is full")

            turn_id = str(uuid.uuid4())
            self._job_q.append((turn_id, text))
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._worker_main,
                    name=f"z-turn-{self.thread_id}",
                    daemon=True,
                )
                self._worker.start()

        return {
            "turnId": turn_id,
            "threadId": self.thread_id,
            "accepted": True,
            "queued": False,
            "stub": False,
        }

    def respond(self, request_id: str, response: Any, text: Optional[str] = None) -> bool:
        io = self._io
        if io is None:
            return False
        return io.deliver_response(request_id, response, text=text)

    def cancel(self) -> dict[str, Any]:
        io = self._io
        if io is not None:
            io.set_cancelled()
        return {
            "ok": True,
            "turnId": self._current_turn_id,
            "threadId": self.thread_id,
        }

    def _worker_main(self) -> None:
        while True:
            with self._lock:
                if not self._job_q:
                    self._worker = None
                    return
                turn_id, text = self._job_q.pop(0)
            try:
                self._run_turn_chain(turn_id, text)
            except Exception:
                logger.exception("turn worker crashed thread=%s", self.thread_id)

    def _ensure_coder(self) -> None:
        if self._coder is not None:
            return

        os.environ.setdefault("Z_CLI", "1")
        # Apply gateway / router env for this process (once).
        try:
            from aider.z.gateway_client import apply_gateway_env_for_router
            from aider.z.onboarding import load_config

            cfg = load_config()
            if cfg.auth_mode == "router" and cfg.selected_model:
                apply_gateway_env_for_router(selected_model=cfg.selected_model)
        except Exception:
            logger.debug("gateway env apply skipped", exc_info=True)

        self._io = AppServerIO(
            notify=self._notify,
            turn_id_provider=self._turn_id,
            root=self.workspace_root,
        )

        from aider.models import Model
        from aider.coders import Coder
        from aider.repo import GitRepo
        from aider.z.onboarding import load_config
        from aider.z.gateway_client import openai_compatible_model, router_uses_gateway

        cfg = load_config()
        model_id = cfg.selected_model or os.environ.get("AIDER_MODEL") or "gpt-4o-mini"
        if cfg.auth_mode == "router" or router_uses_gateway():
            model_id = openai_compatible_model(model_id)

        model = Model(model_id)
        repo = None
        try:
            repo = GitRepo(
                self._io,
                [],
                self.workspace_root,
                models=model.commit_message_models(),
            )
        except Exception as err:
            logger.warning("GitRepo init failed for %s: %s", self.workspace_root, err)

        # Coder.create uses a class-as-self pattern — pass model positionally.
        self._coder = Coder.create(
            model,
            None,
            self._io,
            repo=repo,
            fnames=[],
            use_git=repo is not None,
            stream=True,
            auto_commits=False,
            suggest_shell_commands=True,
            map_tokens=1024,
        )
        # Announce once
        try:
            for line in self._coder.get_announcements():
                self._io.tool_output(line)
        except Exception:
            pass

    def _run_turn_chain(self, turn_id: str, first_text: str) -> None:
        self._ensure_coder()
        assert self._io is not None and self._coder is not None

        self._io.clear_cancelled()
        self._busy = True
        self._current_turn_id = turn_id
        self._notify(
            "turn/started",
            {"turnId": turn_id, "threadId": self.thread_id},
        )

        message = first_text
        ok = True
        interrupted = False
        final_text = None
        try:
            while message:
                self._current_turn_id = turn_id if message is first_text else str(uuid.uuid4())
                if message is not first_text:
                    self._notify(
                        "turn/started",
                        {
                            "turnId": self._current_turn_id,
                            "threadId": self.thread_id,
                            "fromQueue": True,
                        },
                    )
                try:
                    final_text = self._coder.run(with_message=message)
                except KeyboardInterrupt:
                    interrupted = True
                    ok = False
                    break
                except Exception as err:
                    ok = False
                    self._notify(
                        "turn/error",
                        {
                            "turnId": self._current_turn_id,
                            "message": str(err),
                            "detail": traceback.format_exc()[-2000:],
                        },
                    )
                    break

                if self._io._cancel.is_set():
                    interrupted = True
                    ok = False
                    break

                # Drain one queued follow-up (Busy queue) as next turn
                message = self._io.pop_queued_user_message()
                if message:
                    turn_id = self._current_turn_id or turn_id
        finally:
            self._notify(
                "turn/completed",
                {
                    "turnId": self._current_turn_id,
                    "threadId": self.thread_id,
                    "ok": ok,
                    "interrupted": interrupted,
                    "finalText": (final_text or "")[:50000] if final_text else None,
                },
            )
            self._busy = False
            # Keep current_turn_id for a beat so late responds can still match
            # but mark not busy.


class TurnManager:
    """Session-scoped registry of ThreadTurnRunner instances."""

    def __init__(self, *, workspace_root: Optional[str], notify: NotifyFn):
        self.workspace_root = workspace_root
        self._notify = notify
        self._threads: Dict[str, ThreadTurnRunner] = {}
        self._lock = threading.Lock()

    def set_workspace(self, root: str) -> None:
        self.workspace_root = root

    def _runner(self, thread_id: str) -> ThreadTurnRunner:
        if not self.workspace_root:
            raise RuntimeError("No workspace open — call workspace/open first")
        with self._lock:
            runner = self._threads.get(thread_id)
            if runner is None or runner.workspace_root != str(
                Path(self.workspace_root).resolve()
            ):
                runner = ThreadTurnRunner(
                    thread_id=thread_id,
                    workspace_root=self.workspace_root,
                    notify=self._notify,
                )
                self._threads[thread_id] = runner
            return runner

    def start(self, *, text: str, thread_id: str = "default") -> dict:
        return self._runner(thread_id).start_turn(text)

    def respond(
        self,
        *,
        request_id: str,
        response: Any = None,
        text: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> bool:
        with self._lock:
            runners = list(self._threads.values())
        if thread_id:
            r = self._threads.get(thread_id)
            runners = [r] if r else []
        for r in runners:
            if r and r.respond(request_id, response, text=text):
                return True
        return False

    def cancel(self, *, thread_id: str = "default") -> dict:
        return self._runner(thread_id).cancel()
