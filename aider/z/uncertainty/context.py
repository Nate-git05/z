"""Repo maturity and scaffold heuristics — reduce noise in greenfield work."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Sequence

RepoMaturity = Literal["greenfield", "young", "mature"]

_SCAFFOLD_NAMES = {
    "readme.md",
    "readme.rst",
    "readme.txt",
    "license",
    "license.txt",
    "license.md",
    "changelog.md",
    "contributing.md",
    ".gitignore",
    "py.typed",
    "__init__.py",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "package.json",
    "tsconfig.json",
    "makefile",
    "dockerfile",
    ".env.example",
}

_SKIP_DIR_PARTS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
}


def is_scaffold_file(path: str) -> bool:
    name = Path(path.replace("\\", "/")).name.lower()
    if name in _SCAFFOLD_NAMES:
        return True
    if name.startswith("."):
        return True
    return False


def count_code_files(root: Path, *, limit: int = 80) -> int:
    """Count non-scaffold source-ish files under root (capped for speed)."""
    root = Path(root)
    if not root.is_dir():
        return 0
    n = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIR_PARTS for part in p.parts):
            continue
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_scaffold_file(rel):
            continue
        suf = p.suffix.lower()
        if suf in {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".go",
            ".rs",
            ".java",
            ".rb",
            ".php",
            ".cs",
        }:
            n += 1
            if n >= limit:
                return n
    return n


def assess_repo_maturity(root: Path) -> RepoMaturity:
    """
    greenfield: almost no real code peers (empty / just scaffolding)
    young: some code, conventions still forming
    mature: enough peers that pattern misfit is meaningful
    """
    n = count_code_files(Path(root), limit=80)
    if n < 6:
        return "greenfield"
    if n < 25:
        return "young"
    return "mature"


def should_emit_pattern_misfit(maturity: RepoMaturity) -> bool:
    return maturity == "mature"


def should_emit_new_file_noise(maturity: RepoMaturity) -> bool:
    """New-file-no-pattern is expected in greenfield/young — don't alarm."""
    return maturity == "mature"


def filter_scaffold_files(files: Sequence[str]) -> list[str]:
    return [f for f in files if not is_scaffold_file(f)]


def prioritize_nodes(nodes: list, *, limit: int = 8) -> list:
    """Keep the most actionable nodes: highest risk, then lowest confidence."""
    from .schema import TIER_RANK, Tier

    conf_rank = {Tier.LOW: 0, Tier.MEDIUM: 1, Tier.HIGH: 2}

    def key(n):
        return (
            TIER_RANK.get(n.risk_tier, 9),
            conf_rank.get(n.confidence_tier, 9),
            n.title,
        )

    return sorted(nodes, key=key)[:limit]
