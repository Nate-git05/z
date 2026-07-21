"""Read-only explore pass — thin list or bounded deep scout.

Not a second peer agent: runs locally (rg / path / signature peek), injects a
short block into ``cur_messages``, then the main coder continues.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_STOP = frozenset(
    """
    a an the and or to for of in on with without from into about how why what
    please can could would should fix add create implement update change make
    this that these those file files code repo project bug issue error
    """.split()
)

_SIG_RE = re.compile(
    r"^(?:async\s+)?def\s+\w+\s*\([^)]*\)\s*(?:->[^:]+)?:|"
    r"^class\s+\w+|"
    r"^export\s+(?:default\s+)?(?:async\s+)?function\s+\w+|"
    r"^(?:export\s+)?(?:async\s+)?function\s+\w+|"
    r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+\w+|"
    r"^(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\(",
    re.MULTILINE,
)

DEFAULT_SCOUT_CHARS = 2800
DEFAULT_SCOUT_FILES = 5


def explore_pass_enabled() -> bool:
    raw = os.environ.get("Z_EXPLORE_PASS", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def explore_depth() -> str:
    """
    ``deep`` (default) peeks signatures / related tests.
    ``thin`` restores the path-only list from tranche 2.
    """
    raw = (os.environ.get("Z_EXPLORE_DEPTH") or "deep").strip().lower()
    if raw in ("0", "thin", "shallow", "basic", "list"):
        return "thin"
    return "deep"


def scout_char_budget() -> int:
    raw = os.environ.get("Z_EXPLORE_SCOUT_CHARS", "").strip()
    if raw.isdigit():
        return max(800, int(raw))
    return DEFAULT_SCOUT_CHARS


def scout_file_limit() -> int:
    raw = os.environ.get("Z_EXPLORE_SCOUT_FILES", "").strip()
    if raw.isdigit():
        return max(1, min(12, int(raw)))
    return DEFAULT_SCOUT_FILES


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


def _search_rg(
    root: Path,
    keyword: str,
    *,
    max_hits: int = 8,
    timeout: float = 8.0,
) -> List[Tuple[str, str]]:
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
            timeout=max(0.5, float(timeout)),
            cwd=str(root),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    hits: List[Tuple[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            continue
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


def _search_path_names(
    root: Path,
    keyword: str,
    *,
    max_hits: int = 6,
    max_files_scanned: int = 4000,
    deadline: Optional[float] = None,
) -> List[str]:
    """Filename substring search with hard scan/time budgets.

    Unbounded ``os.walk`` on a monorepo can hang for minutes after the
    capability-plan lines and look like Z stopped. Cap both files visited
    and wall clock so explore always returns.
    """
    import time

    key = keyword.lower()
    found: List[str] = []
    skip_dirs = {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "vendor",
        "third_party",
    }
    scanned = 0
    try:
        root_res = root.resolve()
        for dirpath, dirnames, filenames in os.walk(root):
            if deadline is not None and time.monotonic() > deadline:
                break
            dirnames[:] = [
                d for d in dirnames if d not in skip_dirs and not d.startswith(".")
            ]
            for name in filenames:
                scanned += 1
                if scanned > max_files_scanned:
                    return found
                if deadline is not None and time.monotonic() > deadline:
                    return found
                if key in name.lower():
                    try:
                        rel = str(
                            (Path(dirpath) / name).resolve().relative_to(root_res)
                        )
                    except Exception:
                        continue
                    found.append(rel)
                    if len(found) >= max_hits:
                        return found
    except OSError:
        return found
    return found


def peek_signatures(
    path: Path,
    *,
    max_sigs: int = 8,
    max_bytes: int = 12000,
) -> List[str]:
    """Extract a few top-level-ish signatures from a source file."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    found: List[str] = []
    for m in _SIG_RE.finditer(raw):
        sig = m.group(0).strip()
        if len(sig) > 140:
            sig = sig[:140] + "…"
        if sig not in found:
            found.append(sig)
        if len(found) >= max_sigs:
            break
    return found


def suggest_related_paths(root: Path, rel: str) -> List[str]:
    """
    Cheap related-file guesses that exist on disk (tests / sibling init).
    """
    root = Path(root)
    p = Path(rel)
    stem = p.stem
    parent = p.parent
    candidates = [
        parent / "tests" / f"test_{stem}.py",
        parent / f"test_{stem}.py",
        parent / f"{stem}_test.py",
        parent / f"{stem}.test.ts",
        parent / f"{stem}.test.js",
        parent / f"{stem}.spec.ts",
        parent / "__tests__" / f"{stem}.test.ts",
        Path("tests") / f"test_{stem}.py",
        Path("tests") / "test_visible.py",
        parent / "__init__.py",
    ]
    # Also: hidden_tests neighbor for benchmark-ish trees
    if parent.name not in ("", "."):
        candidates.append(Path("tests") / f"test_{parent.name}.py")

    out: List[str] = []
    seen = {rel.replace("\\", "/")}
    for c in candidates:
        abs_c = root / c
        if not abs_c.is_file():
            continue
        try:
            r = str(abs_c.resolve().relative_to(root.resolve())).replace("\\", "/")
        except Exception:
            continue
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
        if len(out) >= 3:
            break
    return out


def _rank_candidates(
    task: str,
    root: Path,
    *,
    already_in_chat: Optional[Sequence[str]],
    max_keywords: int,
    max_files: int,
    on_progress=None,
) -> Tuple[List[str], List[Tuple[str, List[str]]]]:
    keywords = extract_keywords(task, limit=max_keywords)
    if not keywords:
        return [], []

    import time

    in_chat = {str(x).replace("\\", "/") for x in (already_in_chat or [])}
    file_hits: Dict[str, List[str]] = {}
    use_rg = _rg_available()
    # Whole-pass budget — explore must not dominate turn latency.
    pass_deadline = time.monotonic() + 12.0
    total = len(keywords)

    for i, kw in enumerate(keywords):
        remaining = pass_deadline - time.monotonic()
        if remaining <= 0:
            break
        if on_progress:
            try:
                on_progress(
                    f"Planning — exploring `{kw}` ({i + 1}/{total})…"
                )
            except Exception:
                pass
        if use_rg:
            for rel, snip in _search_rg(
                root, kw, max_hits=5, timeout=min(4.0, remaining)
            ):
                file_hits.setdefault(rel.replace("\\", "/"), []).append(f"{kw}: {snip}")
            # rg already covers content; skip unbounded filename walks.
            continue
        for rel in _search_path_names(
            root,
            kw,
            max_hits=4,
            max_files_scanned=2500,
            deadline=pass_deadline,
        ):
            file_hits.setdefault(rel.replace("\\", "/"), []).append(f"filename~{kw}")

    ranked = sorted(
        file_hits.items(),
        key=lambda kv: (kv[0] in in_chat, -len(kv[1]), kv[0]),
    )
    ranked = [kv for kv in ranked if kv[0] not in in_chat][:max_files] or ranked[:max_files]
    return keywords, ranked


def _format_thin(ranked: List[Tuple[str, List[str]]]) -> str:
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


def _format_deep(
    root: Path,
    keywords: List[str],
    ranked: List[Tuple[str, List[str]]],
    *,
    budget: int,
    peek_n: int,
) -> str:
    lines = [
        "# Explore scout (read-only, deep)",
        "Bounded local scout — not a second agent. `/add` before editing.",
        "",
    ]
    if keywords:
        lines.append("Keywords: " + ", ".join(f"`{k}`" for k in keywords[:6]))
        lines.append("")

    for i, (rel, notes) in enumerate(ranked):
        lines.append(f"### `{rel}`")
        if notes:
            lines.append(f"- hit: {notes[0]}")
        if i < peek_n:
            abs_path = root / rel
            if abs_path.is_file():
                sigs = peek_signatures(abs_path)
                if sigs:
                    lines.append("- signatures:")
                    for sig in sigs[:6]:
                        lines.append(f"  - `{sig}`")
                related = suggest_related_paths(root, rel)
                if related:
                    lines.append("- related: " + ", ".join(f"`{r}`" for r in related))
        lines.append("")

    lines.append(
        "Investigate the top candidates next. Do not invent SEARCH/REPLACE for "
        "files that are not in the chat."
    )
    text = "\n".join(lines).rstrip() + "\n"
    if len(text) <= budget:
        return text
    cut = text[: budget - 40]
    nl = cut.rfind("\n")
    if nl > budget // 2:
        cut = cut[:nl]
    return cut.rstrip() + "\n… [explore scout truncated]\n"


def run_explore_pass(
    task: str,
    *,
    root: Path | str,
    already_in_chat: Optional[Sequence[str]] = None,
    max_keywords: int = 5,
    max_files: int = 12,
    depth: Optional[str] = None,
    on_progress=None,
) -> str:
    """
    Return a compact markdown findings block (may be empty).

    ``depth`` overrides ``Z_EXPLORE_DEPTH`` when provided (``thin`` / ``deep``).
    ``on_progress`` is an optional ``callable(str)`` for live status updates.
    """
    if not explore_pass_enabled():
        return ""
    root_p = Path(root)
    if not root_p.is_dir():
        return ""

    if on_progress:
        try:
            on_progress("Planning — exploring related files…")
        except Exception:
            pass

    keywords, ranked = _rank_candidates(
        task,
        root_p,
        already_in_chat=already_in_chat,
        max_keywords=max_keywords,
        max_files=max_files,
        on_progress=on_progress,
    )
    if not ranked:
        return ""

    mode = (depth or explore_depth()).strip().lower()
    if mode in ("0", "thin", "shallow", "basic", "list"):
        return _format_thin(ranked)
    if on_progress:
        try:
            on_progress("Planning — reading candidate signatures…")
        except Exception:
            pass
    return _format_deep(
        root_p,
        keywords,
        ranked,
        budget=scout_char_budget(),
        peek_n=scout_file_limit(),
    )
