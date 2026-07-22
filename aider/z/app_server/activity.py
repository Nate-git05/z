"""Turn activity strip — counters + throttled ``turn/activity`` notifications."""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional, Set

NotifyFn = Callable[[str, dict], None]

_MIN_EMIT_INTERVAL_S = 0.1  # ≤10 Hz

_PHASE_ALIASES = (
    ("choosing model", "choosing_model"),
    ("selecting model", "choosing_model"),
    ("escalat", "choosing_model"),
    ("planning", "planning"),
    ("skill", "planning"),
    ("explore", "planning"),
    ("plan ", "planning"),
    ("applying", "editing"),
    ("edit", "editing"),
    ("search", "searching"),
    ("grep", "searching"),
    ("running", "running"),
    ("shell", "running"),
    ("command", "running"),
    ("mcp", "mcp"),
    ("tool", "mcp"),
    ("waiting for model", "thinking"),
    ("thinking", "thinking"),
    ("waiting", "waiting"),
)


def map_phase_id(phase_or_label: Optional[str]) -> Optional[str]:
    """Map orchestrator phase / busy label → strip phase id."""
    text = (phase_or_label or "").strip().lower()
    if not text:
        return None
    for needle, phase_id in _PHASE_ALIASES:
        if needle in text:
            return phase_id
    return "thinking"


def line_delta_from_edit(original: str, updated: str) -> tuple[int, int]:
    """Approximate +/- lines from a SEARCH/REPLACE pair."""
    o = (original or "").splitlines()
    u = (updated or "").splitlines()
    # New file / whole replace: count both sides; empty SEARCH → create.
    return len(u), len(o)


class TurnActivityTracker:
    """Accumulates per-turn activity and emits ``turn/activity`` (throttled)."""

    def __init__(
        self,
        notify: NotifyFn,
        turn_id_provider: Callable[[], Optional[str]],
    ) -> None:
        self._notify = notify
        self._turn_id_provider = turn_id_provider
        self.reset()

    def reset(self) -> None:
        self.phase: Optional[str] = None
        self.model_id: Optional[str] = None
        self.editing_files: Set[str] = set()
        self.explored_files: Set[str] = set()
        self.searches = 0
        self.commands = 0
        self.mcp_calls = 0
        self.lines_added = 0
        self.lines_removed = 0
        self._dirty = False
        self._last_emit = 0.0
        self._last_payload: Optional[dict] = None

    def set_phase(self, phase: Optional[str]) -> None:
        mapped = map_phase_id(phase) if phase and phase not in {
            "idle",
            "thinking",
            "planning",
            "editing",
            "searching",
            "running",
            "mcp",
            "choosing_model",
            "waiting",
            "queued",
        } else phase
        if mapped == self.phase:
            return
        self.phase = mapped
        self._dirty = True

    def set_model(self, model_id: Optional[str]) -> None:
        mid = (model_id or "").strip() or None
        if mid == self.model_id:
            return
        self.model_id = mid
        self._dirty = True

    def note_edit(self, path: str, *, added: int = 0, removed: int = 0) -> None:
        if path:
            self.editing_files.add(str(path))
        if added > 0:
            self.lines_added += int(added)
        if removed > 0:
            self.lines_removed += int(removed)
        self.set_phase("editing")
        self._dirty = True

    def note_edits(
        self,
        paths: Any,
        *,
        lines_added: int = 0,
        lines_removed: int = 0,
    ) -> None:
        for p in paths or ():
            if p:
                self.editing_files.add(str(p))
        if lines_added > 0:
            self.lines_added += int(lines_added)
        if lines_removed > 0:
            self.lines_removed += int(lines_removed)
        if paths or lines_added or lines_removed:
            self.set_phase("editing")
            self._dirty = True

    def note_explore(self, path: Optional[str] = None) -> None:
        if path:
            self.explored_files.add(str(path))
        else:
            # Anonymous explore pass still counts as one explored unit.
            self.explored_files.add(f"__explore_{len(self.explored_files)}")
        self.set_phase("planning")
        self._dirty = True

    def note_search(self, n: int = 1) -> None:
        self.searches += max(0, int(n))
        self.set_phase("searching")
        self._dirty = True

    def note_command(self, command: Optional[str] = None) -> None:
        self.commands += 1
        cmd = (command or "").lower()
        if re.search(r"\b(rg|grep|ag|ack|findstr)\b", cmd) or "git grep" in cmd:
            self.searches += 1
            self.set_phase("searching")
        else:
            self.set_phase("running")
        self._dirty = True

    def note_mcp(self) -> None:
        self.mcp_calls += 1
        self.set_phase("mcp")
        self._dirty = True

    def note_z_tool(self, name: str, args: str = "") -> None:
        name = (name or "").strip().lower()
        if name in ("grep", "search"):
            self.note_search(1)
        elif name in ("read", "glob", "ls", "list"):
            path = (args or "").strip().split()[0] if args else None
            self.note_explore(path or None)
        else:
            self.note_explore(None)

    def observe_tool_output(self, text: str) -> None:
        """Best-effort parse of IO tool_output lines into counters."""
        if not text:
            return
        line = text.strip()
        if not line:
            return

        m = re.match(r"Applied edit to (.+)$", line)
        if m:
            self.note_edit(m.group(1).strip())
            return

        m = re.match(r"Running (.+)$", line)
        if m:
            self.note_command(m.group(1))
            return

        if line.startswith("Exploring related files") or line.startswith("Explore "):
            self.note_explore(None)
            return

        m = re.match(r"MCP:\s*([^\s.]+)\.(\S+)", line)
        if m:
            self.note_mcp()
            return

        m = re.match(r"##\s*`(\w+)\s*(.*)`", line)
        if m:
            self.note_z_tool(m.group(1), m.group(2) or "")
            return

        if "Executed " in line and "tool(s)" in line:
            m = re.search(r"Executed (\d+) tool", line)
            if m:
                n = int(m.group(1))
                for _ in range(n):
                    self.note_explore(None)
            return

    def payload(self) -> dict:
        return {
            "turnId": self._turn_id_provider(),
            "editingFiles": len(self.editing_files),
            "exploredFiles": len(self.explored_files),
            "searches": self.searches,
            "commands": self.commands,
            "mcpCalls": self.mcp_calls,
            "linesAdded": self.lines_added,
            "linesRemoved": self.lines_removed,
            "modelId": self.model_id,
            "phase": self.phase,
            "fileNames": sorted(self.editing_files)[:12],
        }

    def flush(self, *, force: bool = False) -> None:
        if not force and not self._dirty:
            return
        now = time.monotonic()
        if not force and (now - self._last_emit) < _MIN_EMIT_INTERVAL_S:
            return
        payload = self.payload()
        if not force and payload == self._last_payload:
            self._dirty = False
            return
        try:
            self._notify("turn/activity", payload)
        except Exception:
            return
        self._last_emit = now
        self._last_payload = payload
        self._dirty = False

    def maybe_flush(self) -> None:
        """Flush if dirty and interval elapsed (call from hot paths)."""
        if self._dirty:
            self.flush(force=False)
