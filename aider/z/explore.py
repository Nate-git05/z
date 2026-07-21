"""Read-only explore pass — compact findings for a thin coding turn.

Not a second peer agent: runs locally (rg/path heuristics), injects a short
block into cur_messages, then the main coder continues.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

_STOP = frozenset(
    """
    a an the and or to for of in on with without from into about how why what
    please can could would should fix add create implement update change make
    this that these those file files code repo project bug issue error
    """.split()
)


def explore_pass_enabled() -> bool:
    raw = os.environ.get("Z_EXPLORE_PASS", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def extract_keywords(task: str, *, limit: int = 8) -> List[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_\./-]{2,}", task or "")
    out: List[str] = []
    seen = set()
    for t in tokens:
        low = t.lower().strip("./")
        if low in _STOP or low in seen:
            continue
        if low.endswith((".py", ".ts", ".js", ".tsx", ".go", ".rs", ".md")):
            out.append(t)
        elif re.search(r"[A-Z]", t) or "_" in t or "/" in t:
            out.append(t)
        elif len(low) >= 4:
            out.append(t)
        else:
            continue
        seen.add(low)
        if len(out) >= limit:
            break
    return out


def _rg_available() -> bool:
    from shutil import which

    return which("rg") is not None


def _search_rg(root: Path, keyword: str, *, max_hits: int = 8) -> List[Tuple[str, str]]:
    try:
        proc = subprocess.run(
            [
                "rg",
                "-n",
                "--hidden",
                "--glob",
                "!.git",
                "--glob",
                "!node_modules",
                "--glob",
                "!.venv",
                "--glob",
                "!**/__pycache__",
                "-m",
                "3",
                "-S",
                keyword,
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=8,
            cwd=str(root),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    hits: List[Tuple[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            continue
        # path:line:content
        parts = line.split(":", 2)
        if len(parts) < 2:
            continue
        rel = parts[0]
        try:
            rel = str(Path(rel).resolve().relative_to(root.resolve()))
        except Exception:
            pass
        snippet = parts[-1].strip()[:120]
        hits.append((rel, snippet))
        if len(hits) >= max_hits:
            break
    return hits


def _search_path_names(root: Path, keyword: str, *, max_hits: int = 6) -> List[str]:
    key = keyword.lower()
    found: List[str] = []
    skip_dirs = {".git", "node_modules", ".venv", "__pycache__", "dist", "build", ".tox"}
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
            for name in filenames:
                if key in name.lower():
                    try:
                        rel = str((Path(dirpath) / name).resolve().relative_to(root.resolve()))
                    except Exception:
                        continue
                    found.append(rel)
                    if len(found) >= max_hits:
                        return found
    except OSError:
        return found
    return found


def run_explore_pass(
    task: str,
    *,
    root: Path | str,
    already_in_chat: Optional[Sequence[str]] = None,
    max_keywords: int = 5,
    max_files: int = 12,
) -> str:
    """
    Return a compact markdown findings block (may be empty).
    """
    if not explore_pass_enabled():
        return ""
    root_p = Path(root)
    if not root_p.is_dir():
        return ""
    keywords = extract_keywords(task, limit=max_keywords)
    if not keywords:
        return ""

    in_chat = {str(x).replace("\\", "/") for x in (already_in_chat or [])}
    file_hits: dict[str, List[str]] = {}
    use_rg = _rg_available()

    for kw in keywords:
        if use_rg:
            for rel, snip in _search_rg(root_p, kw, max_hits=5):
                file_hits.setdefault(rel, []).append(f"{kw}: {snip}")
        for rel in _search_path_names(root_p, kw, max_hits=4):
            file_hits.setdefault(rel, []).append(f"filename~{kw}")

    # Prefer files not already in chat
    ranked = sorted(
        file_hits.items(),
        key=lambda kv: (kv[0] in in_chat, -len(kv[1]), kv[0]),
    )
    ranked = [kv for kv in ranked if kv[0] not in in_chat][:max_files] or ranked[:max_files]
    if not ranked:
        return ""

    lines = [
        "# Explore pass (read-only findings)",
        "Candidate files for this task (not yet in chat — `/add` before editing):",
        "",
    ]
    for rel, notes in ranked:
        hint = notes[0] if notes else ""
        lines.append(f"- `{rel}` — {hint}" if hint else f"- `{rel}`")
    lines.append("")
    lines.append(
        "Use these as investigation targets. Do not invent edits for files "
        "not in the chat."
    )
    return "\n".join(lines)
