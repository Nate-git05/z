"""Spiral waiting display — calm loading animation while the agent works.

Replaces the old Agent Runner game with a small rotating Archimedean spiral
in the Z accent palette. Same lifecycle API so coder/repo hooks stay stable:

  disp = waiting_display("Waiting for …")
  disp.start()
  disp.notifyFinish()      # soft wind-down — does not hard-cut
  disp.onEndComplete(cb)
  disp.stop()              # hard stop (interrupt / teardown)

Disable with ``Z_WAITING_GAME=0`` (falls back to the compact mascot spinner).
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from typing import Callable, List, Optional, Sequence, Tuple

from .theme import ACCENT, ACCENT_BRIGHT, TEXT_DIM, TEXT_MUTED

# Canvas size (odd) — compact, readable on narrow terminals
_SIZE = 7
_TICK = 0.048
_SHOW_AFTER_S = 0.30
_END_COLLAPSE_S = 0.50
_POINTS = 64


def _ansi_fg(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"\033[38;2;{r};{g};{b}m"


_RESET = "\033[0m"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"
_CLEAR_LINE = "\033[2K"


def _supports_unicode() -> bool:
    if not sys.stdout.isatty():
        return False
    try:
        encoding = getattr(sys.stdout, "encoding", None) or ""
        "·".encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError, TypeError):
        return False


def _supports_truecolor() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    colorterm = (os.environ.get("COLORTERM") or "").lower()
    term = (os.environ.get("TERM") or "").lower()
    return "truecolor" in colorterm or "24bit" in colorterm or "256color" in term


def spiral_cells(
    phase: float,
    *,
    size: int = _SIZE,
    n_points: int = _POINTS,
    turns: float = 2.0,
    scale: float = 1.0,
) -> List[Tuple[int, int, float]]:
    """
    Sample a thin Archimedean spiral arm.

    Returns (row, col, t) where ``t`` is 0 at the core and 1 at the leading tip.
    ``phase`` rotates the whole spiral.
    """
    cx = cy = (size - 1) / 2.0
    max_r = ((size - 1) / 2.0 - 0.15) * max(0.05, min(1.0, scale))
    ordered: List[Tuple[int, int, float]] = []
    seen: set[Tuple[int, int]] = set()
    last: Optional[Tuple[float, float]] = None
    for i in range(n_points):
        frac = i / max(1, n_points - 1)  # 0 core → 1 tip
        theta = phase + frac * turns * 2.0 * math.pi
        r = frac * max_r
        x = cx + r * math.cos(theta)
        y = cy + r * math.sin(theta)
        col = int(round(x))
        row = int(round(y))
        if not (0 <= row < size and 0 <= col < size):
            continue
        key = (row, col)
        if key in seen:
            continue
        if last is not None:
            dx = x - last[0]
            dy = y - last[1]
            if (dx * dx + dy * dy) < 1.15:
                continue
        seen.add(key)
        ordered.append((row, col, frac))
        last = (x, y)
    return ordered


def render_spiral_frame(
    phase: float,
    label: str = "",
    *,
    size: int = _SIZE,
    scale: float = 1.0,
    unicode_ok: bool = True,
    color: bool = True,
    elapsed_s: float = 0.0,
) -> List[str]:
    """Build the multi-line spiral + optional label line for one animation frame."""
    cells = {(r, c): t for r, c, t in spiral_cells(phase, size=size, scale=scale)}
    head = "●" if unicode_ok else "@"
    mid = "•" if unicode_ok else "*"
    tail = "·" if unicode_ok else "."
    empty = " "

    fg_head = _ansi_fg(ACCENT_BRIGHT) if color else ""
    fg_mid = _ansi_fg(ACCENT) if color else ""
    fg_tail = _ansi_fg(TEXT_DIM) if color else ""
    fg_label = _ansi_fg(TEXT_MUTED) if color else ""
    reset = _RESET if color else ""

    lines: List[str] = []
    for row in range(size):
        parts: List[str] = []
        for col in range(size):
            t = cells.get((row, col))
            if t is None:
                parts.append(empty + empty)
                continue
            if t >= 0.80:
                glyph = f"{fg_head}{head}{reset}" if color else head
            elif t >= 0.30:
                glyph = f"{fg_mid}{mid}{reset}" if color else mid
            else:
                glyph = f"{fg_tail}{tail}{reset}" if color else tail
            parts.append(glyph + empty)
        lines.append("".join(parts).rstrip())

    if label:
        mins, secs = divmod(int(max(0.0, elapsed_s)), 60)
        clock = f"{mins}:{secs:02d}" if mins else f"{secs}s"
        status = f"  {label}  ·  {clock}"
        if color:
            lines.append(f"{fg_label}{status}{reset}")
        else:
            lines.append(status)
    return lines


class SpiralWaiting:
    """
    Compact rotating spiral shown while the agent works.

    Compatible with prior waiting hooks: start/stop plus soft-end
    notifyFinish() / onEndComplete(cb).
    """

    def __init__(self, text: str = "Working", *, size: int = _SIZE):
        self.text = text
        self.size = size if size % 2 == 1 else size + 1
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[], None]] = []
        self._end_done = threading.Event()
        self._phase = "idle"  # idle | running | ending | done
        self._end_t0 = 0.0
        self._started_at = 0.0
        self._drawn_rows = 0
        self._visible = False
        self.is_tty = sys.stdout.isatty()
        self.unicode_ok = _supports_unicode()
        self.color = _supports_truecolor()
        self.fancy = bool(self.is_tty and self.unicode_ok)
        self._fallback = None

    # --- public API ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.fancy:
            from .mascot import MascotSpinner

            self._fallback = MascotSpinner(self.text)
            self._fallback.is_tty = self.is_tty
            self._fallback.start()
            return
        self._stop.clear()
        self._end_done.clear()
        self._phase = "running"
        self._started_at = time.time()
        self._visible = False
        self._drawn_rows = 0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def notifyFinish(self) -> None:
        """Soft end — spiral collapses inward, then resolves."""
        if self._fallback is not None:
            self._fallback.stop()
            self._fallback = None
            self._fire_end()
            return
        with self._lock:
            if self._phase in ("ending", "done"):
                return
            self._phase = "ending"
            self._end_t0 = time.time()

    def onEndComplete(self, cb: Callable[[], None]) -> None:
        self._callbacks.append(cb)
        if self._phase == "done" or (
            self._fallback is None and self._end_done.is_set()
        ):
            try:
                cb()
            except Exception:
                pass

    def stop(self) -> None:
        """Hard stop (interrupt / teardown). Prefer notifyFinish() normally."""
        self._stop.set()
        if self._fallback is not None:
            self._fallback.stop()
            self._fallback = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.2)
        self._clear()
        self._phase = "done"
        self._fire_end()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        if self._phase not in ("ending", "done") and not self._stop.is_set():
            self.notifyFinish()
            self._end_done.wait(timeout=1.5)
        self.stop()

    # --- internals ----------------------------------------------------------

    def _fire_end(self) -> None:
        if self._end_done.is_set():
            return
        self._end_done.set()
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception:
                pass

    def _draw(self, lines: Sequence[str]) -> None:
        if not self.is_tty:
            return
        # Move up over previously drawn rows, then rewrite
        out = []
        if self._drawn_rows:
            out.append(f"\033[{self._drawn_rows}A")
        for line in lines:
            out.append(f"{_CLEAR_LINE}{line}\n")
        # Clear any leftover rows if the new frame is shorter
        for _ in range(max(0, self._drawn_rows - len(lines))):
            out.append(f"{_CLEAR_LINE}\n")
        if self._drawn_rows > len(lines):
            out.append(f"\033[{self._drawn_rows - len(lines)}A")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self._drawn_rows = len(lines)

    def _clear(self) -> None:
        if not self.is_tty or not self._visible:
            self._drawn_rows = 0
            self._visible = False
            return
        if self._drawn_rows:
            sys.stdout.write(f"\033[{self._drawn_rows}A")
            for _ in range(self._drawn_rows):
                sys.stdout.write(f"{_CLEAR_LINE}\n")
            sys.stdout.write(f"\033[{self._drawn_rows}A")
        sys.stdout.write(_SHOW_CURSOR)
        sys.stdout.flush()
        self._drawn_rows = 0
        self._visible = False

    def _loop(self) -> None:
        try:
            sys.stdout.write(_HIDE_CURSOR)
            sys.stdout.flush()
            t0 = time.time()
            angle = 0.0
            while not self._stop.is_set():
                now = time.time()
                with self._lock:
                    phase = self._phase

                if phase == "running":
                    if not self._visible and now - t0 < _SHOW_AFTER_S:
                        time.sleep(_TICK)
                        continue
                    if not self._visible:
                        self._visible = True
                    angle += 0.38
                    elapsed = now - self._started_at
                    frame = render_spiral_frame(
                        angle,
                        self.text,
                        size=self.size,
                        scale=1.0,
                        unicode_ok=self.unicode_ok,
                        color=self.color,
                        elapsed_s=elapsed,
                    )
                    self._draw(frame)
                    time.sleep(_TICK)
                    continue

                if phase == "ending":
                    elapsed_end = now - self._end_t0
                    if elapsed_end >= _END_COLLAPSE_S:
                        break
                    # Collapse inward + keep a little rotation
                    progress = elapsed_end / _END_COLLAPSE_S
                    scale = max(0.0, 1.0 - progress)
                    angle += 0.22
                    frame = render_spiral_frame(
                        angle,
                        self.text if progress < 0.7 else "",
                        size=self.size,
                        scale=scale,
                        unicode_ok=self.unicode_ok,
                        color=self.color,
                        elapsed_s=now - self._started_at,
                    )
                    self._draw(frame)
                    time.sleep(_TICK)
                    continue

                break
        finally:
            self._clear()
            self._phase = "done"
            self._fire_end()


def waiting_display(text: str, *, interactive: bool | None = None):
    """
    Factory used by the coder/repo wait hooks.

    ``Z_WAITING_GAME=0`` disables the spiral → compact mascot spinner.
    """
    env = os.environ.get("Z_WAITING_GAME", "1").strip().lower()
    want = interactive if interactive is not None else env not in (
        "0",
        "false",
        "no",
        "off",
    )
    if want and sys.stdout.isatty():
        return SpiralWaiting(text)
    from .mascot import MascotSpinner

    return MascotSpinner(text)


# Back-compat aliases (older imports / tests)
AgentRunnerGame = SpiralWaiting
MascotRunnerGame = SpiralWaiting
