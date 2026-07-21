"""Latency helpers inspired by thin-shell agents (e.g. T3 Code).

Goals that do NOT weaken Z's core (plan / verify / uncertainty):
  - Overlap independent local work (explore vs checklist/plan)
  - Defer non-critical I/O (Chroma reindex) off the turn critical path
  - Fail-fast verification when relevant tests already cover the change

T3 Code itself is a GUI over Codex/Claude — most of its "speed" is not
running a control plane. These helpers only steal the *orchestration*
patterns (fork non-critical work, coalesce, fail-fast) that fit Z.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional

# Shared pool for turn-local background work (explore, chroma). Keep small —
# we only overlap a few CPU/IO tasks per user turn.
_POOL: Optional[ThreadPoolExecutor] = None
_POOL_LOCK = threading.Lock()


def latency_overlap_enabled() -> bool:
    """Master switch. Default ON. Set Z_LATENCY_OVERLAP=0 to serialize."""
    raw = os.environ.get("Z_LATENCY_OVERLAP", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _pool() -> ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="z-latency")
        return _POOL


def submit_background(fn: Callable, *args, **kwargs) -> Future:
    """Run ``fn`` on the shared background pool."""
    return _pool().submit(fn, *args, **kwargs)


def join_future(fut: Optional[Future], *, timeout: float = 12.0):
    """
    Wait for a background future. On timeout/error return None (caller skips).
    Never raises into the turn loop.
    """
    if fut is None:
        return None
    try:
        return fut.result(timeout=max(0.5, float(timeout)))
    except Exception:
        try:
            fut.cancel()
        except Exception:
            pass
        return None
