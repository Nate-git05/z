"""Z mascot — pixel scientist glyph + waiting animation helpers.

Idle pose is used on the startup / login banner (static, ASCII-stable).
Working frames feed the compact spinner; the interactive runner game uses
the taller sprites in ``waiting_game.py``.
"""

from __future__ import annotations

import sys
import threading
import time

from rich.console import Console

from .theme import ACCENT

# Multi-line idle pose: yellow scientist with glasses + lab coat vibe.
# Pure ASCII — macOS Terminal mis-measures rare glyphs and shreds layouts.
IDLE_MASCOT = [
    r"    /^\   ",
    r"   |###|  ",
    r"  [|o o|] ",
    r"   | = |  ",
    r"   /| |\  ",
]

IDLE_MASCOT_ASCII = list(IDLE_MASCOT)

# Compact single-line working frames (run / jump cycle).
# Same width every frame so the status line does not jitter.
WORKING_FRAMES = [
    r"[o.o]  ",  # run A
    r"[o.o]- ",  # run B
    r"[o^o]  ",  # jump
    r"[o.o]_ ",  # land
]

WORKING_FRAMES_ASCII = list(WORKING_FRAMES)


def _supports_unicode() -> bool:
    if not sys.stdout.isatty():
        return False
    try:
        encoding = getattr(sys.stdout, "encoding", None) or ""
        "ᴗ".encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError, TypeError):
        return False


def idle_mascot_lines(unicode_ok: bool | None = None) -> list[str]:
    """Return the idle multi-line mascot for the banner."""
    if unicode_ok is None:
        unicode_ok = _supports_unicode()
    return list(IDLE_MASCOT if unicode_ok else IDLE_MASCOT_ASCII)


def working_mascot_frame(index: int, unicode_ok: bool | None = None) -> str:
    """Return one frame of the working bounce animation."""
    if unicode_ok is None:
        unicode_ok = _supports_unicode()
    frames = WORKING_FRAMES if unicode_ok else WORKING_FRAMES_ASCII
    return frames[index % len(frames)]


def _ansi_color(hex_color: str) -> str:
    """Convert #RRGGBB to an ANSI 24-bit foreground escape."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"\033[38;2;{r};{g};{b}m"


_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[38;2;160;160;160m"


class MascotSpinner:
    """
    Lightweight frame-cycling mascot spinner for active Z work.

    Drop-in replacement for WaitingSpinner: start()/stop() and context manager.
    Prefer ``MascotRunnerGame`` (waiting_game) when an interactive TTY wait is
    wanted; this class stays as the safe non-interactive fallback.
    """

    def __init__(self, text: str = "Working", delay: float = 0.18):
        self.text = text
        self.delay = delay
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.is_tty = sys.stdout.isatty()
        self.console = Console()
        self.unicode_ok = _supports_unicode()
        self.frames = WORKING_FRAMES if self.unicode_ok else WORKING_FRAMES_ASCII
        self.frame_idx = 0
        self.visible = False
        self.start_time = time.time()
        self.last_display_len = 0
        self._accent = _ansi_color(ACCENT)

    def _next_frame(self) -> str:
        frame = self.frames[self.frame_idx]
        self.frame_idx = (self.frame_idx + 1) % len(self.frames)
        return frame

    def step(self, text: str | None = None) -> None:
        if text is not None:
            self.text = text
        if not self.is_tty:
            return

        now = time.time()
        if not self.visible and now - self.start_time >= 0.35:
            self.visible = True
            self.console.show_cursor(False)

        if not self.visible:
            return

        frame = self._next_frame()
        max_width = max(0, self.console.width - 2)
        plain = f"{frame} {self.text}"
        if len(plain) > max_width:
            plain = plain[:max_width]
            if len(frame) < max_width:
                plain = frame + (" " + self.text)[: max_width - len(frame)]
            else:
                plain = frame[:max_width]

        padding = " " * max(0, self.last_display_len - len(plain))
        colored = (
            f"{self._accent}{frame}{_ANSI_RESET}"
            f"{_ANSI_DIM}{plain[len(frame):]}{_ANSI_RESET}"
        )
        sys.stdout.write(f"\r{colored}{padding}")
        sys.stdout.flush()
        self.last_display_len = len(plain)

    def end(self) -> None:
        if self.visible and self.is_tty:
            sys.stdout.write("\r" + " " * self.last_display_len + "\r")
            sys.stdout.flush()
            self.console.show_cursor(True)
        self.visible = False

    def _spin(self):
        while not self._stop_event.is_set():
            self.step()
            time.sleep(self.delay)
        self.end()

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self.start_time = time.time()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=self.delay * 2)
        self.end()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
