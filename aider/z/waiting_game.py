"""Agent Runner — autoplay Dino-style loading game for long agent waits.

Spec highlights:
  - Half-block (▀) truecolor scientist sprite (Reference A identity, B technique)
  - Chrome-Dino structure (ground, scroll, score) on a dark terminal palette
  - Autoplays jumps; optional Space/↑ override
  - Alternate screen buffer (no scrollback spam)
  - Smooth ending via notifyFinish() → decelerate → settle → resolve
  - Activates only after a short threshold so fast tasks don't flash

Lifecycle:
  Game.start()
  Game.notifyFinish()   # soft end — does not hard-cut
  Game.onEndComplete(cb)
  Game.stop()           # hard stop (interrupt / teardown)
"""

from __future__ import annotations

import os
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# --- Palette (Reference A) ---------------------------------------------------
HAIR = "#F5A623"
HAIR_S = "#D9820A"
SKIN = "#F0B27A"
OUT = "#161A2E"
WHITE = "#FFFFFF"
COAT = "#F4F4F4"
CUFF = "#9FB8C8"
TIE = "#E5484D"
NAVY = "#161A2E"
BUG = "#E5484D"
BRACE = "#9FB8C8"
DIM = "#6B6B6B"
SCORE_FG = "#A0A0A0"
GROUND = "#6B6B6B"

_PALETTE = {
    "H": HAIR,
    "h": HAIR_S,
    "S": SKIN,
    "O": OUT,
    "W": WHITE,
    "C": COAT,
    "U": CUFF,
    "T": TIE,
    "N": NAVY,
    "B": BUG,
    "R": BRACE,
    "G": GROUND,
    "D": DIM,
    ".": None,
}

# Scientist frames: 14 wide × 16 tall "pixels" (half-block → 14×8 cells)
# Legend: H hair, h shadow, S skin, O outline/glasses, W lens, C coat,
#         U cuff, T tie, N navy, . transparent
_RUN_A = [
    "....HHHHHH....",
    "...HhHHHHHh...",
    "...OSSSSSSO...",
    "...OWSSSSWO...",
    "....SSSSSS....",
    "...CCCTCTCCC..",
    "..UCCCTCTCCCU.",
    "..U.CCCTCTCC.U",
    "....CCCCCC....",
    "....CC..CC....",
    "....NN..NN....",
    "....NN..NN....",
    "....NN..NN....",
    "....NN...N....",
    "....NN........",
    "....NN........",
]
_RUN_B = [
    "....HHHHHH....",
    "...HhHHHHHh...",
    "...OSSSSSSO...",
    "...OWSSSSWO...",
    "....SSSSSS....",
    "...CCCTCTCCC..",
    "..UCCCTCTCCCU.",
    "..U.CCCTCTCC.U",
    "....CCCCCC....",
    "....CC..CC....",
    "....NN..NN....",
    "....NN..NN....",
    "....NN..NN....",
    "...N...NN.....",
    "........NN....",
    "........NN....",
]
_JUMP = [
    "....HHHHHH....",
    "...HhHHHHHh...",
    "...OSSSSSSO...",
    "...OWSSSSWO...",
    "....SSSSSS....",
    "U..CCCTCTCC..U",
    "U.UCCCTCTCCU.U",
    "...CCCTCTCC...",
    "....CCCCCC....",
    "....CC..CC....",
    "....NN..NN....",
    "....NN..NN....",
    "...NN....NN...",
    "...NN....NN...",
    "..............",
    "..............",
]
_IDLE = [
    "....HHHHHH....",
    "...HhHHHHHh...",
    "...OSSSSSSO...",
    "...OWSSSSWO...",
    "....SSSSSS....",
    "...CCCTCTCCC..",
    "...CCCTCTCCC..",
    "....CCCTCTCC..",
    "....CCCCCC....",
    "....CC..CC....",
    "....NN..NN....",
    "....NN..NN....",
    "....NN..NN....",
    "....NN..NN....",
    "....NN..NN....",
    "....NN..NN....",
]

_SPRITE_W = 14
_SPRITE_H = 16  # pixels; terminal rows = 8 with half-blocks

# Obstacles as small half-block pixel maps (width × height pixels)
_OBS_BUG = [
    "..BB..",
    ".BBBB.",
    "BBWBBW",
    ".BBBB.",
    "..BB..",
    ".B..B.",
]
_OBS_BRACE = [
    ".RR.",
    "R...",
    "R...",
    ".RR.",
    "R...",
    "R...",
    ".RR.",
]
_OBS_BANG = [
    ".B.",
    ".B.",
    ".B.",
    "...",
    ".B.",
]

_ENTER_ALT = "\033[?1049h"
_EXIT_ALT = "\033[?1049l"
_HIDE_CUR = "\033[?25l"
_SHOW_CUR = "\033[?25h"
_HOME = "\033[H"
_CLEAR = "\033[2J"
_RESET = "\033[0m"

# Timing (spec §7)
_THRESHOLD_S = 1.6
_FPS = 12
_TICK = 1.0 / _FPS
_END_DECEL_S = 0.40
_END_SETTLE_S = 0.30
_END_RESOLVE_S = 0.40


def _hex_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _fg(h: Optional[str]) -> str:
    if not h:
        return ""
    r, g, b = _hex_rgb(h)
    return f"\033[38;2;{r};{g};{b}m"


def _bg(h: Optional[str]) -> str:
    if not h:
        return "\033[49m"
    r, g, b = _hex_rgb(h)
    return f"\033[48;2;{r};{g};{b}m"


def _supports_truecolor() -> bool:
    ct = (os.environ.get("COLORTERM") or "").lower()
    if "truecolor" in ct or "24bit" in ct:
        return True
    # Common capable terminals even without COLORTERM
    term = (os.environ.get("TERM") or "").lower()
    if "256color" in term or term in ("xterm-ghostty", "alacritty", "kitty"):
        return True
    return False


def _supports_unicode() -> bool:
    if not sys.stdout.isatty():
        return False
    try:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        "▀".encode(enc)
        return True
    except (UnicodeEncodeError, LookupError, TypeError):
        return False


def _grid_from_art(rows: Sequence[str]) -> List[List[Optional[str]]]:
    out: List[List[Optional[str]]] = []
    for row in rows:
        out.append([_PALETTE.get(ch, None) for ch in row])
    return out


def _render_half_block(
    grid: List[List[Optional[str]]],
    *,
    transparent_bg: Optional[str] = None,
) -> List[str]:
    """Pack pixel rows into ▀ cells (top=FG, bottom=BG)."""
    if not grid:
        return []
    h = len(grid)
    w = max(len(r) for r in grid)
    # Pad to even height
    rows = [list(r) + [None] * (w - len(r)) for r in grid]
    if h % 2:
        rows.append([None] * w)
        h += 1
    lines: List[str] = []
    for y in range(0, h, 2):
        parts = [_RESET]
        for x in range(w):
            top = rows[y][x]
            bot = rows[y + 1][x]
            if top is None and bot is None:
                parts.append(" ")
                continue
            # Transparent → use terminal default bg; still draw with space if both empty
            fg = top if top is not None else (bot or transparent_bg)
            bg = bot if bot is not None else transparent_bg
            if top is None and bot is not None:
                # Only bottom pixel: use lower half ▄
                parts.append(f"{_fg(bot)}{_bg(transparent_bg)}▄{_RESET}")
            elif top is not None and bot is None:
                parts.append(f"{_fg(top)}{_bg(transparent_bg)}▀{_RESET}")
            else:
                parts.append(f"{_fg(top)}{_bg(bot)}▀{_RESET}")
        lines.append("".join(parts) + _RESET)
    return lines


@dataclass
class _Obstacle:
    x: float
    grid: List[List[Optional[str]]]
    jump_at: float  # x position when auto-jump should fire


@dataclass
class _State:
    phase: str = "waiting"  # waiting | running | ending | done
    score: int = 0
    speed: float = 1.15
    player_y: float = 0.0
    vel_y: float = 0.0
    run_frame: int = 0
    obstacles: List[_Obstacle] = field(default_factory=list)
    spawn_in: float = 0.0
    ticks: int = 0
    end_t0: float = 0.0
    end_beat: int = 0
    jump_pending_clear: bool = False
    started_at: float = field(default_factory=time.time)


class AgentRunnerGame:
    """
    Autoplays a calm endless runner while the agent works.

    Compatible with MascotSpinner callers (start/stop) plus soft-end API:
    notifyFinish() / onEndComplete(cb).
    """

    def __init__(self, text: str = "Working", *, width: int = 56):
        self.text = text
        self.width = max(40, min(width, 72))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[], None]] = []
        self._end_done = threading.Event()
        self.state = _State()
        self.is_tty = sys.stdout.isatty()
        self.truecolor = _supports_truecolor()
        self.unicode_ok = _supports_unicode()
        self.fancy = bool(self.is_tty and self.truecolor and self.unicode_ok)
        self._alt_screen = False
        self._fallback = None
        self._keys_ok = False
        self._old_term = None
        self._fd = None
        self._sprites = {
            "run_a": _grid_from_art(_RUN_A),
            "run_b": _grid_from_art(_RUN_B),
            "jump": _grid_from_art(_JUMP),
            "idle": _grid_from_art(_IDLE),
        }
        self._obs_kinds = [
            _grid_from_art(_OBS_BUG),
            _grid_from_art(_OBS_BRACE),
            _grid_from_art(_OBS_BANG),
        ]

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
        self.state = _State(started_at=time.time(), spawn_in=random.uniform(1.2, 2.4))
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def notifyFinish(self) -> None:
        """Soft end — triggers §7 choreography; does not hard-cut."""
        if self._fallback is not None:
            self._fallback.stop()
            self._fallback = None
            self._fire_end()
            return
        with self._lock:
            if self.state.phase in ("ending", "done"):
                return
            # Let an in-air jump complete before decelerating
            if self.state.player_y > 0.05:
                self.state.jump_pending_clear = True
            self.state.phase = "ending"
            self.state.end_t0 = time.time()
            self.state.end_beat = 0

    def onEndComplete(self, cb: Callable[[], None]) -> None:
        self._callbacks.append(cb)
        if self.state.phase == "done" or (
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
            self._thread.join(timeout=1.5)
        self._teardown_screen()
        self._restore_keys()
        self.state.phase = "done"
        self._fire_end()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        if self.state.phase not in ("ending", "done") and not self._stop.is_set():
            self.notifyFinish()
            self._end_done.wait(timeout=2.0)
        self.stop()

    # --- internals ----------------------------------------------------------

    def _fire_end(self) -> None:
        self._end_done.set()
        cbs = list(self._callbacks)
        self._callbacks.clear()
        for cb in cbs:
            try:
                cb()
            except Exception:
                pass

    def _enter_screen(self) -> None:
        if self._alt_screen:
            return
        try:
            sys.stdout.write(_ENTER_ALT + _HIDE_CUR + _CLEAR + _HOME)
            sys.stdout.flush()
            self._alt_screen = True
        except Exception:
            self._alt_screen = False

    def _teardown_screen(self) -> None:
        if not self._alt_screen:
            return
        try:
            sys.stdout.write(_EXIT_ALT + _SHOW_CUR + _RESET)
            sys.stdout.flush()
        except Exception:
            pass
        self._alt_screen = False

    def _open_keys(self) -> None:
        if not sys.stdin.isatty() or os.name == "nt":
            return
        try:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._old_term = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._keys_ok = True
        except Exception:
            self._keys_ok = False

    def _restore_keys(self) -> None:
        if not self._keys_ok or self._fd is None or self._old_term is None:
            return
        try:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_term)
        except Exception:
            pass
        self._keys_ok = False

    def _read_key(self) -> Optional[str]:
        if not self._keys_ok:
            return None
        try:
            import select

            if not select.select([sys.stdin], [], [], 0)[0]:
                return None
            ch = sys.stdin.read(1)
            if ch == " ":
                return "JUMP"
            if ch == "\x1b" and select.select([sys.stdin], [], [], 0)[0]:
                if sys.stdin.read(1) == "[" and select.select([sys.stdin], [], [], 0)[0]:
                    if sys.stdin.read(1) == "A":
                        return "JUMP"
            return None
        except Exception:
            return None

    def _jump(self) -> None:
        if self.state.player_y <= 0.05 and self.state.phase in ("running", "ending"):
            self.state.vel_y = 2.55
            self.state.player_y = 0.05

    def _spawn(self) -> None:
        kind = random.choice(self._obs_kinds)
        # Auto-jump trigger: when obstacle is this many cols from player
        lead = 9.0 + random.uniform(-0.6, 0.6)  # ±~80ms at typical speed
        self.state.obstacles.append(
            _Obstacle(x=float(self.width - 2), grid=kind, jump_at=lead)
        )
        self.state.spawn_in = random.uniform(1.2, 2.6)

    def _physics(self, dt: float, *, spawning: bool) -> None:
        st = self.state
        st.ticks += 1
        # Elapsed-time score (tenths of a second) — honest status
        st.score = int((time.time() - st.started_at) * 10)

        if st.player_y > 0 or st.vel_y > 0:
            st.vel_y -= 0.20
            st.player_y = max(0.0, st.player_y + st.vel_y * dt * 8)
            if st.player_y <= 0:
                st.player_y = 0
                st.vel_y = 0
        else:
            st.run_frame = (st.run_frame + 1) % 4

        for ob in st.obstacles:
            ob.x -= st.speed * dt * 18
        # Auto-jump with small timing jitter already baked into jump_at
        for ob in st.obstacles:
            if 2.0 < ob.x <= ob.jump_at and st.player_y <= 0.05:
                self._jump()
                break
        st.obstacles = [o for o in st.obstacles if o.x + 6 > 0]

        if spawning:
            st.spawn_in -= dt
            if st.spawn_in <= 0 and len(st.obstacles) < 3:
                self._spawn()
            # Cosmetic speed creep
            st.speed = min(2.2, 1.15 + (time.time() - st.started_at) * 0.02)

    def _player_grid(self) -> List[List[Optional[str]]]:
        st = self.state
        if st.phase == "ending" and st.end_beat >= 2:
            return self._sprites["idle"]
        if st.player_y > 0.15:
            return self._sprites["jump"]
        return self._sprites["run_a" if (st.run_frame // 2) % 2 == 0 else "run_b"]

    def _blit(
        self,
        canvas: List[List[Optional[str]]],
        sprite: List[List[Optional[str]]],
        x0: int,
        y0: int,
    ) -> None:
        ch = len(canvas)
        cw = len(canvas[0]) if canvas else 0
        for dy, row in enumerate(sprite):
            y = y0 + dy
            if y < 0 or y >= ch:
                continue
            for dx, pix in enumerate(row):
                if pix is None:
                    continue
                x = x0 + dx
                if 0 <= x < cw:
                    canvas[y][x] = pix

    def _compose_frame(self) -> List[str]:
        # Pixel canvas: width cols × ~20 pixel rows → 10 terminal rows
        pw = self.width
        ph = 20
        canvas: List[List[Optional[str]]] = [[None] * pw for _ in range(ph)]
        ground_y = 16

        # Ground speckles / dashed line
        for x in range(pw):
            if (x + self.state.ticks) % 3 == 0:
                canvas[ground_y][x] = GROUND
            else:
                canvas[ground_y][x] = DIM

        # Obstacles sit on ground
        for ob in self.state.obstacles:
            oh = len(ob.grid)
            self._blit(canvas, ob.grid, int(ob.x), ground_y - oh)

        # Player
        lift = int(round(self.state.player_y * 2))
        sprite = self._player_grid()
        sh = len(sprite)
        self._blit(canvas, sprite, 3, ground_y - sh - lift)

        body = _render_half_block(canvas)
        # HUD
        hud = f"{self.text[:28]}".ljust(max(0, pw - 14)) + f"{self.state.score:6d}"
        hud_line = f"{_fg(SCORE_FG)}{hud[:pw]}{_RESET}"
        return [hud_line, *body]

    def _draw(self, lines: Sequence[str]) -> None:
        try:
            sys.stdout.write(_HOME)
            for line in lines:
                sys.stdout.write(f"\033[2K{line}\n")
            # Clear leftover rows below
            for _ in range(2):
                sys.stdout.write("\033[2K\n")
            sys.stdout.flush()
        except Exception:
            pass

    def _ease_out(self, t: float) -> float:
        t = max(0.0, min(1.0, t))
        return 1.0 - (1.0 - t) ** 3

    def _loop(self) -> None:
        self._open_keys()
        visible = False
        try:
            while not self._stop.is_set():
                now = time.time()
                st = self.state

                # Optional manual jump easter egg
                if self._read_key() == "JUMP":
                    self._jump()

                # Threshold before first paint
                if not visible:
                    if now - st.started_at < _THRESHOLD_S:
                        time.sleep(_TICK)
                        continue
                    self._enter_screen()
                    visible = True
                    st.phase = "running"

                if st.phase == "running":
                    self._physics(_TICK, spawning=True)
                    self._draw(self._compose_frame())

                elif st.phase == "ending":
                    # Wait for jump to land if needed
                    if st.jump_pending_clear and st.player_y > 0.05:
                        self._physics(_TICK, spawning=False)
                        self._draw(self._compose_frame())
                        time.sleep(_TICK)
                        continue
                    st.jump_pending_clear = False
                    elapsed = now - st.end_t0

                    if elapsed < _END_DECEL_S:
                        # Beat 1 — decelerate (ease-out), stop spawning
                        t = elapsed / _END_DECEL_S
                        factor = 1.0 - self._ease_out(t)
                        base_speed = st.speed
                        st.speed = max(0.0, base_speed * factor)
                        # Don't let speed get re-ramped
                        self._physics(_TICK, spawning=False)
                        st.speed = max(0.0, base_speed * factor)
                        st.end_beat = 1
                        self._draw(self._compose_frame())
                    elif elapsed < _END_DECEL_S + _END_SETTLE_S:
                        # Beat 2 — settle to idle
                        st.speed = 0.0
                        st.player_y = 0.0
                        st.vel_y = 0.0
                        st.obstacles = []
                        st.end_beat = 2
                        self._draw(self._compose_frame())
                    elif elapsed < _END_DECEL_S + _END_SETTLE_S + _END_RESOLVE_S:
                        # Beat 3 — collapse / fade rows
                        st.end_beat = 3
                        progress = (
                            elapsed - _END_DECEL_S - _END_SETTLE_S
                        ) / _END_RESOLVE_S
                        lines = self._compose_frame()
                        keep = max(0, int(len(lines) * (1.0 - progress)))
                        collapsed = lines[:keep] + [""] * (len(lines) - keep)
                        self._draw(collapsed)
                    else:
                        st.phase = "done"
                        break
                else:
                    break

                time.sleep(_TICK)
        finally:
            self._teardown_screen()
            self._restore_keys()
            self.state.phase = "done"
            self._fire_end()


def waiting_display(text: str, *, interactive: bool | None = None):
    """
    Factory used by the coder/repo wait hooks.

    ``Z_WAITING_GAME=0`` / ``--`` env disables the runner → compact spinner.
    """
    env = os.environ.get("Z_WAITING_GAME", "1").strip().lower()
    want = interactive if interactive is not None else env not in (
        "0",
        "false",
        "no",
        "off",
    )
    if want and sys.stdout.isatty():
        return AgentRunnerGame(text)
    from .mascot import MascotSpinner

    return MascotSpinner(text)


# Back-compat alias for earlier import sites / tests
MascotRunnerGame = AgentRunnerGame
