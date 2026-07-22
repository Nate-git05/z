"""Turn trace — resolved step titles/excerpts for Chat (Phase 2).

Emits ``turn/step`` notifications. Prefer resolve-only emits (no running UI).
See ``docs/app/z-agent-state-trace-plan.md``.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

NotifyFn = Callable[[str, dict], None]

_MAX_STEPS = 40
_EXCERPT_MAX = 280
_TITLE_MAX = 72


def _basename(path: str) -> str:
    try:
        return Path(path).name or path
    except Exception:
        return path


def _excerpt(text: str, limit: int = _EXCERPT_MAX) -> Optional[str]:
    t = " ".join((text or "").split()).strip()
    if not t:
        return None
    if len(t) > limit:
        return t[: limit - 1] + "…"
    return t


def _title_from_text(text: str, fallback: str = "Thought") -> str:
    raw = (text or "").strip()
    if not raw:
        return fallback
    first = raw.split("\n", 1)[0].strip()
    first = re.sub(r"^[#>*\-\s]+", "", first)
    first = re.sub(r"^(thinking|reason(ing)?)\s*[:\-]\s*", "", first, flags=re.I)
    if len(first) > _TITLE_MAX:
        first = first[: _TITLE_MAX - 1] + "…"
    return first if len(first) >= 4 else fallback


def _short_cmd(cmd: str) -> str:
    one = " ".join((cmd or "").split())
    if len(one) > 48:
        return one[:47] + "…"
    return one


class TurnTraceTracker:
    """Accumulate per-turn steps and emit ``turn/step`` upserts."""

    def __init__(
        self,
        notify: NotifyFn,
        turn_id_provider: Callable[[], Optional[str]],
    ) -> None:
        self._notify = notify
        self._turn_id_provider = turn_id_provider
        self.reset()

    def reset(self) -> None:
        self._seq = 0
        self._steps: dict[str, dict[str, Any]] = {}
        self._thinking_id: Optional[str] = None
        self._thinking_buf: list[str] = []
        self._thinking_started: float = 0.0
        self._mcp_open: dict[str, str] = {}  # callId -> stepId
        self._emitted = 0

    def _turn_id(self) -> Optional[str]:
        try:
            return self._turn_id_provider()
        except Exception:
            return None

    def _new_id(self) -> str:
        self._seq += 1
        return f"s{self._seq}"

    def _emit(self, step: dict[str, Any]) -> None:
        if self._emitted >= _MAX_STEPS and step.get("stepId") not in self._steps:
            return
        sid = str(step.get("stepId") or "")
        if sid and sid not in self._steps:
            self._emitted += 1
        if sid:
            self._steps[sid] = step
        try:
            self._notify("turn/step", dict(step))
        except Exception:
            pass

    def open_thinking(self) -> None:
        """Start buffering a thinking step (emit only on close)."""
        if self._thinking_id:
            return
        self._thinking_id = self._new_id()
        self._thinking_buf = []
        self._thinking_started = time.monotonic()

    def append_reasoning(self, text: str) -> None:
        if not text:
            return
        if not self._thinking_id:
            self.open_thinking()
        self._thinking_buf.append(str(text))

    def close_thinking_if_open(
        self,
        *,
        status: str = "done",
        resolution_label: Optional[str] = None,
    ) -> None:
        if not self._thinking_id:
            return
        buf = "".join(self._thinking_buf)
        # Skip empty thinking steps (model produced no reasoning content).
        if not buf.strip() and status == "done":
            self._thinking_id = None
            self._thinking_buf = []
            self._thinking_started = 0.0
            return
        duration_ms = None
        if self._thinking_started:
            duration_ms = int(max(0, (time.monotonic() - self._thinking_started) * 1000))
        label = resolution_label
        if not label:
            if status == "needs_input":
                label = "Needs input"
            elif status == "blocked":
                label = "Blocked"
            elif status == "cancelled":
                label = "Cancelled"
            else:
                label = "Done"
        step = {
            "turnId": self._turn_id(),
            "stepId": self._thinking_id,
            "kind": "thinking",
            "title": _title_from_text(buf, "Thought"),
            "excerpt": _excerpt(buf),
            "status": status,
            "resolutionLabel": label,
            "durationMs": duration_ms,
        }
        self._thinking_id = None
        self._thinking_buf = []
        self._thinking_started = 0.0
        self._emit(step)

    def _resolve_step(
        self,
        *,
        kind: str,
        title: str,
        excerpt: Optional[str] = None,
        status: str = "done",
        resolution_label: str = "Done",
        step_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        self.close_thinking_if_open()
        sid = step_id or self._new_id()
        step = {
            "turnId": self._turn_id(),
            "stepId": sid,
            "kind": kind,
            "title": title[:_TITLE_MAX],
            "excerpt": excerpt,
            "status": status,
            "resolutionLabel": resolution_label,
        }
        if duration_ms is not None:
            step["durationMs"] = duration_ms
        self._emit(step)

    def note_edit(self, paths: Any, *, lines_added: int = 0, lines_removed: int = 0) -> None:
        names = [_basename(str(p)) for p in (paths or ()) if p]
        if not names:
            title = "Edited files"
        elif len(names) == 1:
            title = f"Edited {names[0]}"
        else:
            title = f"Edited {len(names)} files"
        parts = []
        if lines_added:
            parts.append(f"+{lines_added}")
        if lines_removed:
            parts.append(f"−{lines_removed}")
        if names:
            parts.append(", ".join(names[:6]))
        self._resolve_step(
            kind="edit",
            title=title,
            excerpt=_excerpt(" · ".join(parts) if parts else title),
        )

    def note_mcp_started(self, *, server: str, tool: str, call_id: str) -> None:
        self.close_thinking_if_open()
        cid = str(call_id or "").strip()
        if not cid:
            return
        sid = self._new_id()
        self._mcp_open[cid] = sid
        # Resolve-only UI: hold until finished (no running emit).

    def note_mcp_finished(
        self,
        *,
        server: str,
        tool: str,
        call_id: str,
        ok: bool = True,
        summary: Optional[str] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        self.close_thinking_if_open()
        cid = str(call_id or "").strip()
        sid = self._mcp_open.pop(cid, None) or self._new_id()
        title = f"{server}.{tool}".strip(".")
        if ok:
            self._resolve_step(
                kind="mcp",
                title=title or "MCP tool",
                excerpt=_excerpt(summary or ""),
                status="done",
                resolution_label="Done",
                step_id=sid,
                duration_ms=duration_ms,
            )
        else:
            self._resolve_step(
                kind="mcp",
                title=title or "MCP tool",
                excerpt=_excerpt(error or "failed"),
                status="blocked",
                resolution_label="Blocked",
                step_id=sid,
                duration_ms=duration_ms,
            )

    def mark_waiting(self, *, kind: str, question: str) -> None:
        """Flip in-flight thinking (or emit a wait step) when user input is required."""
        q = (question or "").strip()
        status = "needs_input"
        label = "Needs input"
        if (kind or "").lower() in ("mcp_tool",) or "block" in (kind or "").lower():
            status = "blocked"
            label = "Blocked"
        if self._thinking_id:
            self.close_thinking_if_open(status=status, resolution_label=label)
            return
        title = _title_from_text(q, "Waiting for input")
        self._resolve_step(
            kind="other",
            title=title,
            excerpt=_excerpt(q),
            status=status,
            resolution_label=label,
        )

    def observe_tool_line(self, text: str) -> None:
        """Map IO tool_output lines into resolved trace steps."""
        if not text:
            return
        line = text.strip()
        if not line:
            return

        m = re.match(r"Applied edit to (.+)$", line)
        if m:
            # apply_updates hook owns edit steps; ignore IO echo.
            return

        m = re.match(r"Running (.+)$", line)
        if m:
            cmd = m.group(1).strip()
            low = cmd.lower()
            if re.search(r"\b(rg|grep|ag|ack|findstr)\b", low) or "git grep" in low:
                self._resolve_step(
                    kind="search",
                    title=f"Searched for “{_short_cmd(cmd)}”",
                    excerpt=_excerpt(cmd),
                )
            else:
                self._resolve_step(
                    kind="shell",
                    title=f"Ran {_short_cmd(cmd)}",
                    excerpt=_excerpt(cmd),
                )
            return

        if line.startswith("Exploring related files") or line.startswith("Explore "):
            self._resolve_step(
                kind="read",
                title="Explored related files",
                excerpt=_excerpt(line),
            )
            return

        m = re.match(r"MCP:\s*([^\s.]+)\.(\S+)", line)
        if m:
            # Structured mcp_finished owns these steps; ignore IO echo.
            return

        m = re.match(r"##\s*`(\w+)\s*(.*)`", line)
        if m:
            name = (m.group(1) or "").lower()
            args = (m.group(2) or "").strip()
            if name in ("grep", "search"):
                q = _short_cmd(args) or "code"
                web = bool(re.search(r"\bweb\b|search_web|brave|bing", args, re.I))
                self._resolve_step(
                    kind="search_web" if web else "search",
                    title=(
                        f"Searched the web for “{q}”"
                        if web
                        else f"Searched for “{q}”"
                    ),
                    excerpt=_excerpt(args or line),
                )
            elif name in ("read", "glob", "ls", "list"):
                target = args.split()[0] if args else ""
                title = f"Read {_basename(target)}" if target else "Read files"
                self._resolve_step(kind="read", title=title, excerpt=_excerpt(args or line))
            else:
                self._resolve_step(
                    kind="other",
                    title=_title_from_text(f"{name} {args}".strip(), name or "Tool"),
                    excerpt=_excerpt(line),
                )
            return

    def finalize(self, *, ok: bool = True, interrupted: bool = False) -> None:
        if interrupted or not ok:
            self.close_thinking_if_open(status="cancelled", resolution_label="Cancelled")
        else:
            self.close_thinking_if_open(status="done", resolution_label="Done")
        # Drop dangling MCP opens as cancelled
        for cid, sid in list(self._mcp_open.items()):
            self._resolve_step(
                kind="mcp",
                title="MCP tool",
                status="cancelled",
                resolution_label="Cancelled",
                step_id=sid,
            )
        self._mcp_open.clear()

    def snapshot(self) -> dict[str, Any]:
        return {
            "turnId": self._turn_id(),
            "steps": list(self._steps.values()),
        }
