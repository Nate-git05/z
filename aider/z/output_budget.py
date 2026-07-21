"""Budget large tool/shell output — persist full text, return a short preview.

OpenCode-aligned defaults (2000 lines / 50 KiB). The model sees a head+tail
preview plus a path to the full file under ``$Z_HOME/tool-output/``.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024


def budget_enabled() -> bool:
    raw = os.environ.get("Z_TOOL_OUTPUT_BUDGET", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def max_lines() -> int:
    raw = os.environ.get("Z_TOOL_OUTPUT_MAX_LINES", "").strip()
    if raw.isdigit():
        return max(50, int(raw))
    return DEFAULT_MAX_LINES


def max_bytes() -> int:
    raw = os.environ.get("Z_TOOL_OUTPUT_MAX_BYTES", "").strip()
    if raw.isdigit():
        return max(1024, int(raw))
    return DEFAULT_MAX_BYTES


def tool_output_dir() -> Path:
    home = os.environ.get("Z_HOME") or str(Path.home() / ".z")
    d = Path(home) / "tool-output"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _needs_budget(text: str, *, lines_limit: int, bytes_limit: int) -> bool:
    if len(text.encode("utf-8", errors="replace")) > bytes_limit:
        return True
    if text.count("\n") + (1 if text and not text.endswith("\n") else 0) > lines_limit:
        # count lines
        return len(text.splitlines()) > lines_limit
    return len(text.splitlines()) > lines_limit


def _preview(text: str, *, lines_limit: int, bytes_limit: int) -> str:
    lines = text.splitlines(keepends=True)
    if len(lines) > lines_limit:
        head_n = max(20, lines_limit // 3)
        tail_n = max(20, lines_limit // 3)
        head = "".join(lines[:head_n])
        tail = "".join(lines[-tail_n:])
        omitted = len(lines) - head_n - tail_n
        body = (
            f"{head}\n"
            f"… [{omitted} lines omitted — full output on disk] …\n"
            f"{tail}"
        )
    else:
        body = text

    raw = body.encode("utf-8", errors="replace")
    if len(raw) > bytes_limit:
        # Keep head and a short tail by bytes
        head = raw[: max(512, bytes_limit * 2 // 3)]
        tail = raw[-max(256, bytes_limit // 4) :]
        body = (
            head.decode("utf-8", errors="replace")
            + "\n… [bytes omitted — full output on disk] …\n"
            + tail.decode("utf-8", errors="replace")
        )
    return body


def persist_full_output(text: str, *, label: str = "output") -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in (label or "output"))[:40]
    name = f"tool_{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe or 'output'}.txt"
    path = tool_output_dir() / name
    path.write_text(text, encoding="utf-8", errors="replace")
    return path


def budget_tool_output(
    text: str,
    *,
    label: str = "command",
    lines_limit: Optional[int] = None,
    bytes_limit: Optional[int] = None,
) -> Tuple[str, Optional[Path]]:
    """
    Return ``(chat_text, saved_path_or_None)``.

    When under budget, returns the original text unchanged and ``None``.
    When over budget, persists the full text and returns a preview + path.
    """
    if not text:
        return text, None
    if not budget_enabled():
        return text, None

    lim_lines = lines_limit if lines_limit is not None else max_lines()
    lim_bytes = bytes_limit if bytes_limit is not None else max_bytes()

    if not _needs_budget(text, lines_limit=lim_lines, bytes_limit=lim_bytes):
        return text, None

    path = persist_full_output(text, label=label)
    preview = _preview(text, lines_limit=lim_lines, bytes_limit=lim_bytes)
    n_lines = len(text.splitlines())
    n_bytes = len(text.encode("utf-8", errors="replace"))
    header = (
        f"[tool-output budgeted] full {n_lines} lines / {n_bytes} bytes saved to:\n"
        f"  {path}\n"
        f"Preview follows (head/tail). Re-read the file if you need more detail.\n"
        f"{'─' * 40}\n"
    )
    return header + preview, path


def inject_tool_result(
    text: str,
    *,
    label: str = "tool",
    command: Optional[str] = None,
) -> str:
    """
    Budget an arbitrary tool/MCP/scrape dump for chat injection.

    Returns the (possibly previewed) text ready for ``cur_messages``.
    """
    budgeted, path = budget_tool_output(text or "", label=label)
    if command:
        return f"Output of `{command}`:\n{budgeted}"
    if path:
        return budgeted
    return budgeted