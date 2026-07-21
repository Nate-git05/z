"""Interactive Chrome-Dino-style runner while Z waits on a long LLM call.

Uses the scientist mascot as the player. Space / ↑ jumps. Runs in a daemon
thread with the same start()/stop() API as MascotSpinner so callers can swap
it in during ``Waiting for <model>``.

Falls back to non-interactive animation when stdin is not a TTY or raw mode
cannot be enabled.
"""

from __future__ import annotations

import os
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from rich.console import Console

from .theme import ACCENT, TEXT_DIM

# Playfield height (sprite rows + ground + HUD)
_FIELD_H = 6
_GROUND_Y = 4  # row index of the ground line inside the field buffer

# Scientist player sprites (run A / run B / jump) — fixed width
_PLAYER_W = 8
_PLAYER_RUN = [
    [
        r"  /^\   ",
        r" [|o.o|]",
        r"  /| |\ ",
    ],
    [
        r"  /^\   ",
        r" [|o.o|]",
        r"  /| |\\",
    ],
]
_PLAYER_JUMP = [
    r"  /^\   ",
    r" [|o^o|]",
    r"  /| |\ ",
]

# Obstacle glyphs (cactus-like bugs) — height 1..3, drawn upward from ground
_OBSTACLE_SHAPES = [
    ["#"],
    ["#", "|"],
    ["#", "#", "|"],
    ["X", "|"],
    ["!", "!", "|"],
]


def _ansi_fg(hex_color: str) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"\033[38;2;{r};{g};{b}m"


_RESET = "\033[0m"
_DIM = _ansi_fg(TEXT_DIM)
_ACCENT = _ansi_fg(ACCENT)
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"
_CLEAR_LINE = "\033[2K"


@dataclass
class _Obstacle:
    x: float
    shape: List[str]  # bottom → top? we store top→bottom for drawing ease: [top,...,base]


@dataclass
class _GameState:
    score: int = 0
    hi: int = 0
    speed: float = 1.4
    player_y: float = 0.0  # 0 = on ground, higher = airborne (rows)
    vel_y: float = 0.0
    run_frame: int = 0
    obstacles: List[_Obstacle] = field(default_factory=list)
    spawn_cooldown: float = 0.0
    dead_flash: float = 0.0
    ticks: int = 0


class _KeyReader:
    """Non-blocking single-key reader; restores terminal on close."""

    def __init__(self):
        self.ok = False
        self._fd = None
        self._old = None
        self._windows = False

    def open(self) -> bool:
        if not sys.stdin.isatty():
            return False
        if os.name == "nt":
            self._windows = True
            self.ok = True
            return True
        try:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self.ok = True
            return True
        except Exception:
            self.ok = False
            return False

    def read(self) -> Optional[str]:
        if not self.ok:
            return None
        if self._windows:
            try:
                import msvcrt

                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch in (b"\x00", b"\xe0") and msvcrt.kbhit():
                        ch2 = msvcrt.getch()
                        if ch2 in (b"H",):  # up arrow
                            return "UP"
                        return None
                    try:
                        return ch.decode("utf-8", errors="ignore")
                    except Exception:
                        return None
            except Exception:
                return None
            return None
        try:
            import select

            if select.select([sys.stdin], [], [], 0)[0]:
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    # Possible arrow sequence
                    if select.select([sys.stdin], [], [], 0)[0]:
                        ch2 = sys.stdin.read(1)
                        if ch2 == "[" and select.select([sys.stdin], [], [], 0)[0]:
                            ch3 = sys.stdin.read(1)
                            if ch3 == "A":
                                return "UP"
                    return None
                return ch
        except Exception:
            return None
        return None

    def close(self) -> None:
        if self._windows or self._fd is None or self._old is None:
            self.ok = False
            return
        try:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
        except Exception:
            pass
        self.ok = False
        self._fd = None
        self._old = None


def _load_hi_score() -> int:
    try:
        from aider.z.paths import ensure_z_home

        path = ensure_z_home() / "runner_hi.txt"
        if path.is_file():
            return max(0, int(path.read_text(encoding="utf-8").strip() or "0"))
    except Exception:
        pass
    return 0


def _save_hi_score(score: int) -> None:
    try:
        from aider.z.paths import ensure_z_home

        path = ensure_z_home() / "runner_hi.txt"
        path.write_text(str(int(score)), encoding="utf-8")
    except Exception:
        pass


class MascotRunnerGame:
    """
    Interactive side-scroller while waiting on the model.

    Drop-in for MascotSpinner: start() / stop() / context manager.
    """

    def __init__(self, text: str = "Waiting", delay: float = 0.05, width: int = 48):
        self.text = text
        self.delay = delay
        self.width = max(28, min(width, 72))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.is_tty = sys.stdout.isatty()
        self.console = Console()
        self.visible = False
        self.start_time = time.time()
        self._keys = _KeyReader()
        self._interactive = False
        self._lines_drawn = 0
        self.state = _GameState(hi=_load_hi_score())
        # Fallback spinner if we cannot take over the TTY interactively
        self._fallback = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self.start_time = time.time()
        self._stop_event = threading.Event()
        self.state = _GameState(hi=_load_hi_score())
        self._interactive = bool(self.is_tty and self._keys.open())
        if not self._interactive:
            # Non-interactive fallback — compact mascot spinner
            from .mascot import MascotSpinner

            self._fallback = MascotSpinner(self.text, delay=0.18)
            self._fallback.is_tty = self.is_tty
            self._fallback.start()
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._fallback is not None:
            self._fallback.stop()
            self._fallback = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._keys.close()
        self._clear_field()
        if self.state.score > self.state.hi:
            _save_hi_score(self.state.score)
        if self.is_tty:
            try:
                sys.stdout.write(_SHOW_CURSOR)
                sys.stdout.flush()
            except Exception:
                pass
        self.visible = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    # --- game internals -----------------------------------------------------

    def _jump(self) -> None:
        if self.state.player_y <= 0.05:
            self.state.vel_y = 2.6
            self.state.player_y = 0.01

    def _tick_physics(self) -> None:
        st = self.state
        st.ticks += 1
        # Gravity
        if st.player_y > 0 or st.vel_y > 0:
            st.vel_y -= 0.22
            st.player_y += st.vel_y * 0.35
            if st.player_y <= 0:
                st.player_y = 0
                st.vel_y = 0
        else:
            st.run_frame = (st.run_frame + 1) % (len(_PLAYER_RUN) * 3)

        # Move obstacles
        for ob in st.obstacles:
            ob.x -= st.speed
        st.obstacles = [o for o in st.obstacles if o.x + 3 > 0]

        # Spawn
        st.spawn_cooldown -= self.delay
        if st.spawn_cooldown <= 0 and len(st.obstacles) < 3:
            gap = random.uniform(0.9, 2.2)
            st.spawn_cooldown = gap
            shape = random.choice(_OBSTACLE_SHAPES)
            st.obstacles.append(_Obstacle(x=float(self.width - 2), shape=list(shape)))

        # Speed ramp + score
        st.speed = min(3.2, 1.4 + st.ticks * 0.0015)
        st.score += 1

        # Collision (player occupies cols 2..2+_PLAYER_W, feet at ground unless jumping)
        if st.dead_flash > 0:
            st.dead_flash -= self.delay
            return
        player_top = 3 + int(st.player_y)  # sprite height ~3 above ground contribution
        # Simpler AABB: if airborne enough, clear short obstacles
        clearance = st.player_y
        px0, px1 = 2, 2 + _PLAYER_W
        for ob in st.obstacles:
            ox0 = int(ob.x)
            ox1 = ox0 + max(1, max(len(row) for row in ob.shape))
            if ox1 < px0 or ox0 > px1:
                continue
            ob_h = len(ob.shape)
            if clearance < ob_h - 0.3:
                # Hit
                st.dead_flash = 0.45
                st.obstacles = [o for o in st.obstacles if o.x > px1 + 2]
                st.score = max(0, st.score - 25)
                break
        del player_top

    def _player_sprite(self) -> List[str]:
        if self.state.player_y > 0.2:
            return list(_PLAYER_JUMP)
        idx = (self.state.run_frame // 3) % len(_PLAYER_RUN)
        return list(_PLAYER_RUN[idx])

    def _render_lines(self) -> List[str]:
        w = self.width
        # Empty sky rows
        rows = [[" "] * w for _ in range(_FIELD_H)]

        # Ground
        for x in range(w):
            rows[_GROUND_Y][x] = "-" if (x + self.state.ticks) % 3 else "="
        # Speckles under ground
        if _GROUND_Y + 1 < _FIELD_H:
            for x in range(0, w, 7):
                rows[_GROUND_Y + 1][x] = "."

        # Obstacles (shape listed top→bottom ending at ground-1)
        for ob in self.state.obstacles:
            ox = int(ob.x)
            h = len(ob.shape)
            for i, ch_row in enumerate(ob.shape):
                # shape[0] is top
                y = _GROUND_Y - h + i
                if y < 0 or y >= _FIELD_H:
                    continue
                for dx, ch in enumerate(ch_row):
                    xx = ox + dx
                    if 0 <= xx < w and ch != " ":
                        rows[y][xx] = ch

        # Player
        sprite = self._player_sprite()
        # Feet sit on ground; jump lifts the whole sprite
        lift = int(round(self.state.player_y))
        base_y = _GROUND_Y - len(sprite) - lift
        for i, line in enumerate(sprite):
            y = base_y + i
            if y < 0 or y >= _FIELD_H:
                continue
            for dx, ch in enumerate(line):
                xx = 2 + dx
                if 0 <= xx < w and ch != " ":
                    rows[y][xx] = ch

        # HUD
        hi = max(self.state.hi, self.state.score)
        hud = f"HI {hi:06d}  {self.state.score:06d}"
        hint = "space=jump"
        status = self.text
        if len(status) > w - 14:
            status = status[: max(0, w - 17)] + "..."
        top = f"{hud}"
        bottom = f"{hint}  {status}"

        out = ["".join(r) for r in rows]
        # Prepend HUD / append hint as extra lines
        return [top[:w].ljust(w), *out, bottom[:w].ljust(w)]

    def _draw(self, lines: Sequence[str]) -> None:
        if not self.is_tty:
            return
        # Move cursor up to overwrite previous field
        buf = []
        if self.visible and self._lines_drawn > 0:
            buf.append(f"\033[{self._lines_drawn}A")
        for line in lines:
            colored = f"{_ACCENT}{line}{_RESET}"
            # Dim the HUD / hint rows slightly
            if line is lines[0] or line is lines[-1]:
                colored = f"{_DIM}{line}{_RESET}"
            buf.append(f"{_CLEAR_LINE}\r{colored}\n")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()
        self._lines_drawn = len(lines)
        self.visible = True

    def _clear_field(self) -> None:
        if not self.is_tty or not self.visible:
            return
        buf = []
        if self._lines_drawn > 0:
            buf.append(f"\033[{self._lines_drawn}A")
        for _ in range(self._lines_drawn):
            buf.append(f"{_CLEAR_LINE}\r\n")
        if self._lines_drawn > 0:
            buf.append(f"\033[{self._lines_drawn}A")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()
        self._lines_drawn = 0

    def _loop(self) -> None:
        try:
            sys.stdout.write(_HIDE_CURSOR)
            sys.stdout.flush()
        except Exception:
            pass
        # Small delay so short waits don't flash the game
        while not self._stop_event.is_set():
            if time.time() - self.start_time >= 0.4:
                break
            time.sleep(0.05)
        while not self._stop_event.is_set():
            # Input
            for _ in range(4):
                key = self._keys.read()
                if key is None:
                    break
                if key in (" ", "UP", "w", "W"):
                    self._jump()
            self._tick_physics()
            # Keep width responsive to terminal
            try:
                self.width = max(28, min(self.console.width - 2, 72))
            except Exception:
                pass
            lines = self._render_lines()
            self._draw(lines)
            time.sleep(self.delay)
        self._clear_field()


def waiting_display(text: str, *, interactive: bool | None = None):
    """
    Factory: interactive runner when TTY + not disabled; else MascotSpinner.

    ``Z_WAITING_GAME=0`` forces the compact spinner.
    ``Z_WAITING_GAME=1`` (default) prefers the interactive runner.
    """
    env = os.environ.get("Z_WAITING_GAME", "1").strip().lower()
    want = interactive if interactive is not None else env not in ("0", "false", "no", "off")
    if want and sys.stdout.isatty() and sys.stdin.isatty():
        return MascotRunnerGame(text)
    from .mascot import MascotSpinner

    return MascotSpinner(text)
