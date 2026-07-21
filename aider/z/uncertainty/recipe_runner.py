"""Parse and optionally run sanitizer recipes from README / SPEC / plan prose.

Fault-plan sanitizer-teeth: prefer executing concrete
``cmake … -D*SAN=ON`` / ``ctest --test-dir build-asan`` lines over leaving
them as markdown the model wrote but never ran.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

RunCmdFn = Callable[..., Tuple[int, str]]

_CMAKE_SAN_RE = re.compile(
    r"(?im)^[ \t]*(?:\$\s*)?(cmake\b[^\n]*?-D\w*(?:ASAN|TSAN|UBSAN|LSAN|SAN)\w*=ON[^\n]*)"
)
_CTEST_SAN_DIR_RE = re.compile(
    r"(?im)^[ \t]*(?:\$\s*)?(ctest\b[^\n]*--test-dir\s+\S*(?:asan|tsan|ubsan|lsan|san)\S*[^\n]*)"
)
_MAKE_SAN_RE = re.compile(
    r"(?im)\b((?:make|ninja)\s+(?:asan|tsan|ubsan|lsan|test-asan|test-tsan|sanitizer)\b[^\n]*)"
)


def sanitizer_recipes_enabled() -> bool:
    raw = os.environ.get("Z_SANITIZER_RECIPES", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def extract_sanitizer_recipes(text: str, *, limit: int = 8) -> List[str]:
    """Return concrete shell recipe lines from markdown / SPEC text."""
    if not text:
        return []
    found: List[str] = []
    seen = set()
    for rx in (_CMAKE_SAN_RE, _CTEST_SAN_DIR_RE, _MAKE_SAN_RE):
        for m in rx.finditer(text):
            cmd = " ".join(m.group(1).strip().split())
            if not cmd or cmd in seen:
                continue
            seen.add(cmd)
            found.append(cmd)
            if len(found) >= limit:
                return found
    return found


def gather_recipe_text(
    root: Path | str,
    *,
    extra_texts: Sequence[str] = (),
    edited: Sequence[str] = (),
) -> str:
    """Load README / common docs plus caller-provided SPEC/plan text."""
    root_p = Path(root)
    chunks: List[str] = [t for t in (extra_texts or ()) if t]
    for name in (
        "README.md",
        "README.rst",
        "README",
        "docs/testing.md",
        "docs/sanitizers.md",
    ):
        path = root_p / name
        if path.is_file():
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="ignore")[:20000])
            except OSError:
                pass
    for rel in edited or ():
        low = str(rel).lower().replace("\\", "/")
        if low.endswith((".md", ".rst", ".txt")) and (
            "readme" in low or "san" in low or "test" in low
        ):
            path = root_p / rel
            if path.is_file():
                try:
                    chunks.append(path.read_text(encoding="utf-8", errors="ignore")[:12000])
                except OSError:
                    pass
    return "\n".join(chunks)


@dataclass
class RecipeRunResult:
    attempted: List[str] = field(default_factory=list)
    ran_ok: bool = False
    last_exit_code: Optional[int] = None
    last_output: str = ""
    last_command: Optional[str] = None


def try_run_sanitizer_recipes(
    root: Path | str,
    recipes: Sequence[str],
    *,
    verbose: bool = False,
    error_print=None,
    run_cmd_fn: Optional[RunCmdFn] = None,
    max_recipes: int = 2,
) -> RecipeRunResult:
    """
    Best-effort execute discovered recipes (budgeted by count).

    Does not claim sanitizer success from unit-test green — only records
    whether the recipe command itself exited 0.
    """
    from aider.run_cmd import run_cmd as _default

    run = run_cmd_fn or _default
    root_p = Path(root)
    out = RecipeRunResult()
    for cmd in list(recipes)[:max_recipes]:
        out.attempted.append(cmd)
        out.last_command = cmd
        try:
            code, text = run(
                cmd, verbose=verbose, error_print=error_print, cwd=str(root_p)
            )
        except Exception as err:  # noqa: BLE001
            out.last_exit_code = 1
            out.last_output = str(err)[-2000:]
            continue
        out.last_exit_code = int(code) if code is not None else 1
        out.last_output = (text or "")[-4000:]
        if out.last_exit_code == 0:
            out.ran_ok = True
            break
    return out
