"""Async outbox for uncertainty remote sync (P0.4).

Local store writes are authoritative. Network sync never blocks the agent loop.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# (connect timeout, read timeout) — never 15s on this path
DEFAULT_TIMEOUT = (1.0, 2.0)
_FLUSH_INTERVAL_S = 0.35
_MAX_ATTEMPTS = 4
_SHUTDOWN_FLUSH_S = 2.5


@dataclass
class SyncEvent:
    node_id: str
    version: str
    payload_fn: Callable[[], bool]
    attempts: int = 0
    enqueued_at: float = field(default_factory=time.time)


class SyncOutbox:
    """In-memory deduping outbox drained by a background thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[str, SyncEvent] = {}  # node_id → latest event
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="z-uncertainty-sync", daemon=True
            )
            self._thread.start()
            atexit.register(self.shutdown)

    def enqueue(self, node_id: str, version: str, payload_fn: Callable[[], bool]) -> None:
        """Non-blocking enqueue; starts worker lazily."""
        if not self._started:
            self.start()
        with self._lock:
            # Dedup by node_id — keep latest version only
            self._pending[node_id] = SyncEvent(
                node_id=node_id, version=version, payload_fn=payload_fn
            )
        self._wake.set()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _drain_batch(self) -> List[SyncEvent]:
        with self._lock:
            batch = list(self._pending.values())
            self._pending.clear()
        return batch

    def _requeue(self, events: List[SyncEvent]) -> None:
        with self._lock:
            for ev in events:
                existing = self._pending.get(ev.node_id)
                # Don't clobber a newer version that arrived while we were sending
                if existing is None or existing.version == ev.version:
                    self._pending[ev.node_id] = ev

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=_FLUSH_INTERVAL_S)
            self._wake.clear()
            if self._stop.is_set():
                break
            self._flush_once()

    def _flush_once(self) -> None:
        batch = self._drain_batch()
        if not batch:
            return
        retry: List[SyncEvent] = []
        for ev in batch:
            ok = False
            try:
                ok = bool(ev.payload_fn())
            except Exception as exc:
                logger.debug("uncertainty sync failed for %s: %s", ev.node_id, exc)
                ok = False
            if not ok:
                ev.attempts += 1
                if ev.attempts < _MAX_ATTEMPTS:
                    # Simple exponential backoff via delayed requeue
                    delay = min(2 ** (ev.attempts - 1), 8) * 0.15
                    time.sleep(delay)
                    retry.append(ev)
        if retry and not self._stop.is_set():
            self._requeue(retry)

    def shutdown(self, timeout: float = _SHUTDOWN_FLUSH_S) -> None:
        """Best-effort flush; never block beyond ``timeout``."""
        self._stop.set()
        self._wake.set()
        deadline = time.time() + max(0.1, timeout)
        # One last drain attempt
        try:
            self._flush_once()
        except Exception:
            pass
        t = self._thread
        if t and t.is_alive():
            remaining = max(0.0, deadline - time.time())
            t.join(timeout=remaining)


_GLOBAL: Optional[SyncOutbox] = None
_GLOBAL_LOCK = threading.Lock()


def get_outbox() -> SyncOutbox:
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            _GLOBAL = SyncOutbox()
        return _GLOBAL


def enqueue_node_sync(node_id: str, version: str, payload_fn: Callable[[], bool]) -> None:
    get_outbox().enqueue(node_id, version, payload_fn)


def reset_outbox_for_tests() -> SyncOutbox:
    """Replace the global outbox (tests only)."""
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is not None:
            try:
                _GLOBAL.shutdown(timeout=0.2)
            except Exception:
                pass
        _GLOBAL = SyncOutbox()
        return _GLOBAL
