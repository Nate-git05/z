"""P3 turn UX — Idle / Busy / WaitingInput orchestrator + message queue.

Owns who has the bottom of the screen so spinner, confirms, and queued next
turns compose as one product loop (see docs/uncertainty/terminal-ux-p3-plan.md).
"""

from __future__ import annotations

import os
import threading
from collections import deque
from enum import Enum
from typing import Callable, Deque, List, Optional, Sequence


DEFAULT_QUEUE_MAX = 20


def turn_queue_enabled(*, z_theme: bool = True) -> bool:
    """Feature flag: ``Z_TURN_QUEUE=0`` disables the Busy queue reader."""
    raw = os.environ.get("Z_TURN_QUEUE")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() not in ("0", "false", "no", "off")
    return bool(z_theme)


class TurnState(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    WAITING_INPUT = "waiting_input"


class TurnOrchestrator:
    """
    Single owner of turn chrome state.

    Queue lives here (also mirrored on InputOutput for convenience).
    Illegal transitions no-op safely rather than raise — TTY must stay alive.
    """

    def __init__(
        self,
        *,
        queue_max: int = DEFAULT_QUEUE_MAX,
        on_state_change: Optional[Callable[[TurnState, Optional[str]], None]] = None,
        on_queue_change: Optional[Callable[[int], None]] = None,
    ):
        self._lock = threading.RLock()
        self.state = TurnState.IDLE
        self.phase: Optional[str] = None
        self.waiting_kind: Optional[str] = None
        self._phase_before_wait: Optional[str] = None
        self._queue: Deque[str] = deque()
        self.queue_max = max(1, int(queue_max))
        self.on_state_change = on_state_change
        self.on_queue_change = on_queue_change
        self._overflow_warned = False

    # --- queue -------------------------------------------------------------

    @property
    def queue_len(self) -> int:
        with self._lock:
            return len(self._queue)

    def list_queue(self) -> List[str]:
        with self._lock:
            return list(self._queue)

    def enqueue(self, text: str) -> bool:
        """
        Append a full next-turn message. Only accepted while Busy (D4).
        Returns True if queued.
        """
        msg = (text or "").strip()
        if not msg:
            return False
        with self._lock:
            if self.state != TurnState.BUSY:
                return False
            if len(self._queue) >= self.queue_max:
                self._overflow_warned = True
                return False
            self._queue.append(msg)
            n = len(self._queue)
        self._emit_queue(n)
        return True

    def enqueue_forced(self, text: str) -> bool:
        """Enqueue regardless of state (Idle clipboard / tests). Still respects max."""
        msg = (text or "").strip()
        if not msg:
            return False
        with self._lock:
            if len(self._queue) >= self.queue_max:
                return False
            self._queue.append(msg)
            n = len(self._queue)
        self._emit_queue(n)
        return True

    def pop_queued(self) -> Optional[str]:
        with self._lock:
            if not self._queue:
                return None
            msg = self._queue.popleft()
            n = len(self._queue)
        self._emit_queue(n)
        return msg

    def clear_queue(self) -> int:
        with self._lock:
            n = len(self._queue)
            self._queue.clear()
        if n:
            self._emit_queue(0)
        return n

    def drop_last_queued(self) -> Optional[str]:
        with self._lock:
            if not self._queue:
                return None
            msg = self._queue.pop()
            n = len(self._queue)
        self._emit_queue(n)
        return msg

    # --- transitions -------------------------------------------------------

    def enter_idle(self) -> None:
        with self._lock:
            self.state = TurnState.IDLE
            self.phase = None
            self.waiting_kind = None
            self._phase_before_wait = None
        self._emit_state(TurnState.IDLE, None)

    def enter_busy(self, phase: str = "working") -> None:
        phase = (phase or "working").strip() or "working"
        with self._lock:
            self.state = TurnState.BUSY
            self.phase = phase
            self.waiting_kind = None
            self._phase_before_wait = None
        self._emit_state(TurnState.BUSY, phase)

    def set_phase(self, phase: str) -> None:
        phase = (phase or "").strip()
        if not phase:
            return
        with self._lock:
            if self.state != TurnState.BUSY:
                return
            self.phase = phase
        self._emit_state(TurnState.BUSY, phase)

    def enter_waiting_input(self, kind: str = "confirm") -> None:
        """Stop Busy chrome for a blocking ask; queue frozen (not discarded)."""
        with self._lock:
            if self.state == TurnState.BUSY:
                self._phase_before_wait = self.phase
            self.state = TurnState.WAITING_INPUT
            self.waiting_kind = (kind or "confirm").strip() or "confirm"
            self.phase = None
            resume = self._phase_before_wait
        self._emit_state(TurnState.WAITING_INPUT, resume)

    def leave_waiting_input(self) -> Optional[str]:
        """
        Return to Busy after a confirm without discarding the queue.
        Returns the phase to resume (caller may restart spinner).
        """
        with self._lock:
            if self.state != TurnState.WAITING_INPUT:
                return None
            phase = self._phase_before_wait or "Working…"
            self.state = TurnState.BUSY
            self.phase = phase
            self.waiting_kind = None
            self._phase_before_wait = None
        self._emit_state(TurnState.BUSY, phase)
        return phase

    def interrupt_busy(self) -> None:
        """
        ^C during Busy: leave Busy chrome, keep queue (D8).
        Does not clear WaitingInput — confirm path handles that separately.
        """
        with self._lock:
            if self.state == TurnState.BUSY:
                self.state = TurnState.IDLE
                self.phase = None
        # Keep queue; emit idle so spinner stops
        self._emit_state(TurnState.IDLE, None)

    # --- labels ------------------------------------------------------------

    def status_label(self, base: Optional[str] = None) -> str:
        """T0 status text including queue count and interrupt hint."""
        with self._lock:
            state = self.state
            phase = self.phase
            n = len(self._queue)
            kind = self.waiting_kind
        if state == TurnState.WAITING_INPUT:
            return (
                f"Waiting for your reply ({kind or 'confirm'}) — "
                f"queued messages pause"
                + (f" · Queued {n}" if n else "")
            )
        if state != TurnState.BUSY:
            return base or ""
        label = (base or phase or "Working…").strip()
        if n:
            label = f"{label}  · Queued {n}"
        if "Ctrl+C" not in label:
            label = f"{label}  · Ctrl+C to interrupt"
        return label

    def format_queued_preview(self, text: str, *, max_len: int = 72) -> str:
        one = " ".join((text or "").split())
        if len(one) > max_len:
            one = one[: max_len - 1] + "…"
        return f"▶ queued: {one}"

    # --- internals ---------------------------------------------------------

    def _emit_state(self, state: TurnState, phase: Optional[str]) -> None:
        cb = self.on_state_change
        if cb:
            try:
                cb(state, phase)
            except Exception:
                pass

    def _emit_queue(self, n: int) -> None:
        cb = self.on_queue_change
        if cb:
            try:
                cb(n)
            except Exception:
                pass


class BusyQueueReader:
    """
    Minimal line reader while Busy — buffers keystrokes until Enter, then enqueues.

    Does **not** open a full PromptSession (D1). Uses a daemon thread + select on
    stdin when available; no-ops on non-TTY / when disabled.
    """

    def __init__(
        self,
        orchestrator: TurnOrchestrator,
        *,
        enabled: bool = True,
        on_enqueued: Optional[Callable[[str, int], None]] = None,
    ):
        self.orchestrator = orchestrator
        self.enabled = enabled
        self.on_enqueued = on_enqueued
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._buffer: List[str] = []
        self._buf_lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.enabled or self.alive:
            return
        import sys

        if not getattr(sys.stdin, "isatty", lambda: False)():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="z-busy-queue", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=0.4)
        self._thread = None
        with self._buf_lock:
            self._buffer.clear()

    def _run(self) -> None:
        import select
        import sys
        import termios
        import tty

        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
        except Exception:
            return
        raw = False

        def _cook() -> None:
            nonlocal raw
            if raw:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                except Exception:
                    pass
                raw = False

        def _raw() -> None:
            nonlocal raw
            if not raw:
                try:
                    tty.setcbreak(fd)
                    raw = True
                except Exception:
                    raw = False

        try:
            while not self._stop.is_set():
                if self.orchestrator.state != TurnState.BUSY:
                    # Must restore cooked mode so WaitingInput confirms work.
                    _cook()
                    if self._stop.wait(0.05):
                        break
                    continue
                _raw()
                if not raw:
                    break
                try:
                    ready, _, _ = select.select([fd], [], [], 0.1)
                except Exception:
                    break
                if not ready:
                    continue
                try:
                    ch = sys.stdin.read(1)
                except Exception:
                    break
                if not ch:
                    break
                if ch in ("\n", "\r"):
                    self._submit_buffer()
                elif ch in ("\x15",):  # Ctrl+U — drop last queued
                    self.orchestrator.drop_last_queued()
                elif ch in ("\x7f", "\b"):
                    with self._buf_lock:
                        if self._buffer:
                            self._buffer.pop()
                elif ch == "\x1b":
                    self._drain_escape(fd)
                elif ord(ch) >= 32:
                    with self._buf_lock:
                        self._buffer.append(ch)
        finally:
            _cook()
            with self._buf_lock:
                self._buffer.clear()

    def _drain_escape(self, fd: int) -> None:
        import select

        # Consume the rest of a CSI sequence quickly
        for _ in range(8):
            ready, _, _ = select.select([fd], [], [], 0.01)
            if not ready:
                break
            try:
                import sys

                sys.stdin.read(1)
            except Exception:
                break

    def _submit_buffer(self) -> None:
        with self._buf_lock:
            text = "".join(self._buffer).strip()
            self._buffer.clear()
        if not text:
            return
        if self.orchestrator.enqueue(text):
            n = self.orchestrator.queue_len
            if self.on_enqueued:
                try:
                    self.on_enqueued(text, n)
                except Exception:
                    pass


def attach_orchestrator_to_io(io, orchestrator: TurnOrchestrator) -> None:
    """Wire IO convenience attributes used across the codebase."""
    io.turn_orchestrator = orchestrator
    io.message_queue = orchestrator  # duck: enqueue/pop/clear via orchestrator
