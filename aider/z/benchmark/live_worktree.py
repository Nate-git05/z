"""Worktree snapshot / diff helpers for the live P2 adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

_SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".z",
    ".pytest_cache",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
}

_SKIP_FILE_NAMES = {
    ".z_p2_live_trace.json",
    ".z_p2_live_snapshot.json",
}


def iter_worktree_files(root: Path) -> Iterable[Path]:
    root = Path(root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.name in _SKIP_FILE_NAMES:
            continue
        if path.name.startswith(".z_p2_"):
            continue
        yield path


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def snapshot_worktree(root: Path) -> Dict[str, str]:
    """Map repo-relative posix paths → content sha256."""
    root = Path(root).resolve()
    out: Dict[str, str] = {}
    for path in iter_worktree_files(root):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        digest = file_digest(path)
        if digest:
            out[rel] = digest
    return out


def diff_worktree(before: Dict[str, str], root: Path) -> List[str]:
    """Return sorted relative paths that were added or modified since ``before``."""
    after = snapshot_worktree(root)
    changed: Set[str] = set()
    for rel, digest in after.items():
        if before.get(rel) != digest:
            changed.add(rel)
    # Deletions are unusual for P2 scoring (edits list is "touched"); omit.
    return sorted(changed)


def seed_fnames_from_globs(
    worktree: Path,
    globs: Optional[Iterable[str]],
) -> List[str]:
    """
    Resolve ``allowed_edit_globs`` (or similar) into absolute paths that exist.
    """
    if not globs:
        return []
    root = Path(worktree).resolve()
    found: List[str] = []
    seen: Set[str] = set()
    for pattern in globs:
        pat = (pattern or "").strip()
        if not pat:
            continue
        # Exact relative path
        direct = root / pat
        if direct.is_file():
            key = str(direct.resolve())
            if key not in seen:
                found.append(key)
                seen.add(key)
            continue
        for match in root.glob(pat):
            if match.is_file():
                key = str(match.resolve())
                if key not in seen:
                    found.append(key)
                    seen.add(key)
    return found
