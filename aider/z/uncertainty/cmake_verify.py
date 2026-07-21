"""CMake verify honesty — reconfigure before ctest when build files change.

Fault-plan slice ``verify-cmake``: a stale ``build/`` must not be treated as
authoritative after ``CMakeLists.txt`` / ``*.cmake`` edits. Prefer
``cmake -S . -B build``, then ``ctest -N`` discovery, and fail closed in NI
when expected change-scoped tests never appear.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Set, Tuple

RunCmdFn = Callable[..., Tuple[int, str]]

BUILD_SYSTEM_NAMES = frozenset(
    {
        "cmakelists.txt",
        "cmakelists.txt.in",
    }
)
BUILD_SYSTEM_SUFFIXES = (".cmake",)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def cmake_reconfigure_enabled() -> bool:
    return _env_bool("Z_CMAKE_RECONFIGURE", True)


def cmake_require_matched_enabled(*, non_interactive: Optional[bool] = None) -> bool:
    """
    Fail closed when expected change tests are missing from ``ctest -N``.

    Default on for NI / --yes-always; also on when env explicitly set.
    Escape: ``Z_CMAKE_REQUIRE_MATCHED=0``.
    """
    raw = os.environ.get("Z_CMAKE_REQUIRE_MATCHED")
    if raw is not None and str(raw).strip() != "":
        return _env_bool("Z_CMAKE_REQUIRE_MATCHED", True)
    if non_interactive is None:
        non_interactive = _detect_ni()
    return bool(non_interactive)


def _detect_ni() -> bool:
    if os.environ.get("Z_FORCE_NI", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            return True
    except Exception:
        pass
    return False


def is_build_system_path(rel: str) -> bool:
    rel_n = str(rel or "").replace("\\", "/").lstrip("./")
    base = Path(rel_n).name.lower()
    if base in BUILD_SYSTEM_NAMES:
        return True
    low = rel_n.lower()
    return any(low.endswith(suf) for suf in BUILD_SYSTEM_SUFFIXES)


def edited_build_system_files(edited: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for rel in edited or ():
        rel_n = str(rel).replace("\\", "/").lstrip("./")
        if not is_build_system_path(rel_n):
            continue
        if rel_n in seen:
            continue
        seen.add(rel_n)
        out.append(rel_n)
    return out


def is_cmake_project(root: Path) -> bool:
    return (Path(root) / "CMakeLists.txt").is_file()


def resolve_cmake_build_dir(root: Path) -> Path:
    """Prefer existing ``build/``; else ``build`` under root."""
    root = Path(root)
    preferred = root / "build"
    if preferred.is_dir():
        return preferred
    for name in ("build-debug", "build-release", "cmake-build-debug", "out"):
        cand = root / name
        if cand.is_dir() and (cand / "CMakeCache.txt").is_file():
            return cand
    return preferred


def cmake_reconfigure_command(root: Path, build_dir: Path) -> str:
    # Keep cache when build_dir exists; cmake -S/-B is idempotent
    return f'cmake -S "{root}" -B "{build_dir}"'


_CTEST_TEST_LINE = re.compile(
    r"(?im)^\s*(?:Test\s+#?\d+:\s*|^\s*\d+/\d+\s+Test\s+#\d+:\s*)([A-Za-z0-9_.:-]+)"
)
_CTEST_N_LINE = re.compile(r"(?im)^\s*Test\s+#(\d+):\s*(\S+)\s*$")
_ADD_TEST_RE = re.compile(
    r"(?im)\badd_test\s*\(\s*(?:NAME\s+)?([A-Za-z_][A-Za-z0-9_]*)"
)
_ADD_EXECUTABLE_RE = re.compile(
    r"(?im)\badd_executable\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)"
)


def parse_ctest_test_names(output: str) -> List[str]:
    """Parse test names from ``ctest -N`` (preferred) or ``ctest`` run output."""
    text = output or ""
    names: List[str] = []
    seen: Set[str] = set()
    for m in _CTEST_N_LINE.finditer(text):
        name = m.group(2).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    if names:
        return names
    for m in _CTEST_TEST_LINE.finditer(text):
        name = m.group(1).strip()
        if name and name not in seen and name.lower() not in ("test", "tests"):
            seen.add(name)
            names.append(name)
    return names


def extract_expected_tests_from_cmake_text(text: str) -> List[str]:
    """Heuristic: add_test(NAME …) and add_executable(…_tests) from CMakeLists."""
    names: List[str] = []
    seen: Set[str] = set()
    for m in _ADD_TEST_RE.finditer(text or ""):
        n = m.group(1)
        if n not in seen:
            seen.add(n)
            names.append(n)
    for m in _ADD_EXECUTABLE_RE.finditer(text or ""):
        n = m.group(1)
        if n.endswith("_tests") or n.endswith("_test") or "test" in n.lower():
            if n not in seen:
                seen.add(n)
                names.append(n)
    return names


def extract_expected_tests_from_edited(
    root: Path,
    edited: Sequence[str],
    *,
    extra_text: str = "",
) -> List[str]:
    """Collect expected ctest names from edited build files + optional SPEC text."""
    root = Path(root)
    names: List[str] = []
    seen: Set[str] = set()

    def _add(n: str) -> None:
        n = (n or "").strip()
        if not n or n in seen:
            return
        seen.add(n)
        names.append(n)

    for rel in edited_build_system_files(edited):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n in extract_expected_tests_from_cmake_text(text):
            _add(n)

    # Path stems like tests/minilfu_tests.c or minilfu_tests
    for rel in edited or ():
        stem = Path(str(rel)).stem
        if stem.endswith("_tests") or stem.endswith("_test"):
            _add(stem)

    if extra_text:
        for n in extract_expected_tests_from_cmake_text(extra_text):
            _add(n)
        for m in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]*(?:_tests|_test))\b", extra_text
        ):
            _add(m.group(1))

    return names


def match_change_tests(
    expected: Sequence[str], discovered: Sequence[str]
) -> List[str]:
    """Return expected names that appear in discovered (case-sensitive first)."""
    disc = {d: d for d in discovered}
    disc_l = {d.lower(): d for d in discovered}
    matched: List[str] = []
    for exp in expected:
        if exp in disc:
            matched.append(disc[exp])
        elif exp.lower() in disc_l:
            matched.append(disc_l[exp.lower()])
    return matched


@dataclass
class CMakeVerifyResult:
    applies: bool = False
    build_dirty: bool = False
    reconfigured: bool = False
    reconfigure_attempted: bool = False
    reconfigure_ok: bool = True
    reconfigure_command: Optional[str] = None
    reconfigure_output: str = ""
    reconfigure_exit_code: Optional[int] = None
    build_dir: Optional[str] = None
    discovered_tests: List[str] = field(default_factory=list)
    expected_tests: List[str] = field(default_factory=list)
    matched_change_tests: List[str] = field(default_factory=list)
    suggested_test_cmd: Optional[str] = None
    require_matched: bool = False
    stale_suite: bool = False
    error: str = ""

    @property
    def reconfigure_failed(self) -> bool:
        return self.reconfigure_attempted and not self.reconfigure_ok


def _default_run_cmd() -> RunCmdFn:
    from aider.run_cmd import run_cmd

    return run_cmd


def prepare_cmake_verify(
    root: Path | str,
    edited: Sequence[str],
    *,
    test_cmd: Optional[str] = None,
    extra_text: str = "",
    verbose: bool = False,
    error_print=None,
    non_interactive: Optional[bool] = None,
    run_cmd_fn: Optional[RunCmdFn] = None,
    skip_reconfigure: bool = False,
) -> CMakeVerifyResult:
    """
    When this is a CMake project and build files were edited (or ctest is the
    runner with a dirty build tree), reconfigure and list tests via ``ctest -N``.
    """
    root_p = Path(root)
    result = CMakeVerifyResult()
    if not is_cmake_project(root_p):
        return result

    build_files = edited_build_system_files(edited)
    result.build_dirty = bool(build_files)
    cmd = (test_cmd or "").strip()
    uses_ctest = "ctest" in cmd or (
        not cmd and (root_p / "build").is_dir()
    )
    if not result.build_dirty and not uses_ctest:
        # CMake project but not using ctest and no build-file edits — no-op
        if not cmd and (root_p / "CMakeLists.txt").is_file():
            # Still offer ctest after ensuring build exists when no other runner
            pass
        else:
            return result

    # Apply when build files dirty, or when we would run ctest
    if not result.build_dirty and "ctest" not in cmd and cmd:
        return result

    result.applies = True
    build_dir = resolve_cmake_build_dir(root_p)
    result.build_dir = str(build_dir)
    result.suggested_test_cmd = f'ctest --test-dir "{build_dir}"'
    result.expected_tests = extract_expected_tests_from_edited(
        root_p, edited, extra_text=extra_text
    )
    result.require_matched = bool(
        result.build_dirty
        and result.expected_tests
        and cmake_require_matched_enabled(non_interactive=non_interactive)
    )

    run = run_cmd_fn or _default_run_cmd()

    if (
        result.build_dirty
        and cmake_reconfigure_enabled()
        and not skip_reconfigure
    ):
        result.reconfigure_attempted = True
        try:
            build_dir.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            result.reconfigure_ok = False
            result.error = f"Cannot create build dir {build_dir}: {err}"
            return result
        cmake_cmd = cmake_reconfigure_command(root_p, build_dir)
        result.reconfigure_command = cmake_cmd
        try:
            code, out = run(
                cmake_cmd,
                verbose=verbose,
                error_print=error_print,
                cwd=str(root_p),
            )
        except Exception as err:  # noqa: BLE001
            result.reconfigure_ok = False
            result.reconfigure_exit_code = 1
            result.error = str(err)
            result.reconfigure_output = str(err)[-2000:]
            return result
        result.reconfigure_exit_code = int(code) if code is not None else 1
        result.reconfigure_output = (out or "")[-4000:]
        if result.reconfigure_exit_code != 0:
            result.reconfigure_ok = False
            result.error = (
                f"cmake reconfigure failed (exit {result.reconfigure_exit_code}). "
                "Stale build/ will not be treated as verified."
            )
            return result
        result.reconfigured = True
        result.reconfigure_ok = True
    elif result.build_dirty and not cmake_reconfigure_enabled():
        result.error = "Z_CMAKE_RECONFIGURE=0 — skipped reconfigure despite build-file edits"

    # Always try ctest -N when we apply (need discovery for matched checks)
    list_cmd = f'ctest -N --test-dir "{build_dir}"'
    try:
        code, out = run(
            list_cmd,
            verbose=verbose,
            error_print=error_print,
            cwd=str(root_p),
        )
    except Exception as err:  # noqa: BLE001
        if result.require_matched:
            result.stale_suite = True
            result.error = result.error or f"ctest -N failed: {err}"
        return result

    result.discovered_tests = parse_ctest_test_names(out or "")
    result.matched_change_tests = match_change_tests(
        result.expected_tests, result.discovered_tests
    )

    if result.require_matched:
        missing = [
            e
            for e in result.expected_tests
            if e not in result.matched_change_tests
            and e.lower() not in {m.lower() for m in result.matched_change_tests}
        ]
        if missing:
            result.stale_suite = True
            result.error = (
                "Change-scoped CMake tests not discovered after "
                f"{'reconfigure' if result.reconfigured else 'ctest -N'}: "
                f"{', '.join(missing)}. "
                f"Discovered: {', '.join(result.discovered_tests) or '(none)'}. "
                "Refusing to treat the old suite alone as verified."
            )

    return result


def apply_cmake_result_to_record(record, cmake: CMakeVerifyResult) -> None:
    """Copy cmake fields onto a VerificationRecord (duck-typed)."""
    if not cmake.applies:
        return
    record.reconfigured = bool(cmake.reconfigured)
    record.discovered_tests = list(cmake.discovered_tests)
    record.matched_change_tests = list(cmake.matched_change_tests)
    record.cmake_reconfigure_command = cmake.reconfigure_command
    record.cmake_expected_tests = list(cmake.expected_tests)
    if cmake.discovered_tests and (
        record.tests_discovered is None or record.tests_discovered == 0
    ):
        # Prefer authoritative ctest -N count when suite output is thin
        record.tests_discovered = len(cmake.discovered_tests)
        record.zero_tests = len(cmake.discovered_tests) == 0
