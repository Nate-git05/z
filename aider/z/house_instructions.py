"""Project house instructions — AGENTS.md as compact coding context.

OpenCode-inspired: load ambient instructions from the repo (and global
``~/.z/AGENTS.md``) without dumping unrelated skill/uncertainty essays.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_BUDGET = 4000


def house_instructions_enabled() -> bool:
    raw = os.environ.get("Z_HOUSE_INSTRUCTIONS", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def body_budget() -> int:
    raw = os.environ.get("Z_HOUSE_INSTRUCTIONS_CHARS", "").strip()
    if raw.isdigit():
        return max(500, int(raw))
    return DEFAULT_BUDGET


def discover_agents_md_paths(root: Path | str) -> List[Path]:
    """
    Collect AGENTS.md from project root upward (stop at filesystem root)
    plus optional global ``$Z_HOME/AGENTS.md``.
    """
    found: List[Path] = []
    seen = set()
    try:
        start = Path(root).resolve()
    except OSError:
        return found

    cur = start
    for _ in range(24):
        candidate = cur / "AGENTS.md"
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if candidate.is_file() and key not in seen:
            found.append(candidate)
            seen.add(key)
        parent = cur.parent
        if parent == cur:
            break
        # Stop climbing out of git worktrees early if .git found at cur
        cur = parent

    # Prefer nearest-to-root order for display: reverse so root-most last?
    # OpenCode walks up and includes all; nearest project first is more useful.
    # `found` is already nearest-first from walk starting at root.

    home = os.environ.get("Z_HOME") or str(Path.home() / ".z")
    global_agents = Path(home) / "AGENTS.md"
    if global_agents.is_file():
        key = str(global_agents.resolve())
        if key not in seen:
            found.append(global_agents)

    return found


def _truncate(text: str, budget: int) -> Tuple[str, bool]:
    text = (text or "").strip()
    if len(text) <= budget:
        return text, False
    cut = text[:budget]
    nl = cut.rfind("\n")
    if nl > budget // 2:
        cut = cut[:nl]
    return cut.rstrip() + "\n… [AGENTS.md truncated]\n", True


def load_house_instructions(
    root: Path | str,
    *,
    budget: Optional[int] = None,
) -> str:
    """Return a compact markdown block, or empty string if none/disabled."""
    if not house_instructions_enabled():
        return ""
    paths = discover_agents_md_paths(root)
    if not paths:
        return ""
    lim = budget if budget is not None else body_budget()
    # Split budget across files, prefer nearest (first)
    per = max(800, lim // max(1, len(paths)))
    parts: List[str] = [
        "# House instructions (AGENTS.md)",
        "Follow these project rules where they apply:",
        "",
    ]
    used = 0
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        body, _ = _truncate(raw, min(per, lim - used))
        if not body:
            continue
        parts.append(f"## From `{path}`")
        parts.append(body)
        parts.append("")
        used += len(body)
        if used >= lim:
            break
    if len(parts) <= 3:
        return ""
    return "\n".join(parts).rstrip() + "\n"
