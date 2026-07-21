"""Plan permission mode — product edits denied; plan artifact only.

OpenCode-inspired: enter plan → research/design → exit plan → implement.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional


def plan_mode_enabled() -> bool:
    raw = os.environ.get("Z_PLAN_MODE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def plans_dir() -> Path:
    home = os.environ.get("Z_HOME") or str(Path.home() / ".z")
    d = Path(home) / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_plan_path(*, stem: str = "plan") -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in (stem or "plan"))[:40]
    name = f"{int(time.time())}_{safe or 'plan'}.md"
    return plans_dir() / name


def is_plan_artifact_path(path: str, *, root: Optional[str] = None) -> bool:
    """True if ``path`` is under the Z plans directory (or repo ``.z/plans/``)."""
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    candidates = [plans_dir().resolve()]
    if root:
        try:
            candidates.append((Path(root) / ".z" / "plans").resolve())
        except OSError:
            pass
    for base in candidates:
        try:
            p.relative_to(base)
            return True
        except ValueError:
            continue
    # Also allow literal names like plans/foo.md written relative to Z_HOME
    parts = {x.lower() for x in p.parts}
    if "plans" in parts and (".z" in parts or str(plans_dir().parent) in str(p)):
        return True
    return False


def format_plan_mode_reminder(plan_path: Optional[Path] = None) -> str:
    dest = str(plan_path) if plan_path else str(plans_dir() / "<task>.md")
    return (
        "# Plan mode (active)\n"
        "- Do NOT edit product source files.\n"
        "- Do NOT run mutating shell commands.\n"
        "- Explore read-only (search, read files already shared).\n"
        f"- Write/update the plan artifact only at: `{dest}`\n"
        "- When the plan is ready, tell the user to run `/plan-exit` to approve and implement.\n"
    )


def format_plan_exit_context(plan_text: str, *, plan_path: Optional[str] = None) -> str:
    header = "# Approved plan (binding) — proceed to implement"
    if plan_path:
        header += f"\nSource: `{plan_path}`"
    body = (plan_text or "").strip()
    if len(body) > 6000:
        body = body[:6000] + "\n… [plan truncated for context]\n"
    return f"{header}\n\n{body}\n"
