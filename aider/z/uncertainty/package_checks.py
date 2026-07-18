"""Package-scoped verification: typecheck/build/lint before tests.

Prefer the cheapest authoritative check in the *nearest* package.json to the
edited files — not the monorepo root guard script. A compiler error is more
diagnostic than a failed ``npm test`` at the workspace root.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from aider.run_cmd import run_cmd


# Script name preference within a package (cheapest / most authoritative first)
_TYPECHECK_SCRIPT_NAMES = (
    "typecheck",
    "type-check",
    "types",
    "check:types",
    "tsc",
    "typecheck:all",
)
_BUILD_SCRIPT_NAMES = ("build", "compile")
_LINT_SCRIPT_NAMES = ("lint", "eslint", "check")

_COMPILER_ERROR_RE = re.compile(
    r"(?i)("
    r"error\s+TS\d+"
    r"|Property\s+'[^']+'\s+does\s+not\s+exist\s+on\s+type"
    r"|Module\s+'[^']+'\s+has\s+no\s+exported\s+member"
    r"|Cannot\s+find\s+(?:name|module|namespace)\s+"
    r"|is\s+not\s+assignable\s+to\s+type"
    r"|Type\s+error:"
    r"|mypy:|"
    r"error:\s+Argument\s+\d+"
    r"|undefined\s+is\s+not\s+an\s+object"
    r")"
)

_ROOT_GUARD_RE = re.compile(
    r"(?i)("
    r"do\s+not\s+run\s+(?:tests?|npm\s+test)\s+from\s+(?:the\s+)?root"
    r"|run\s+tests?\s+from\s+(?:a\s+)?package"
    r"|use\s+.*(?:workspace|package).*test"
    r"|this\s+is\s+a\s+monorepo"
    r"|please\s+cd\s+into"
    r")"
)


@dataclass
class PackageCheck:
    """One package-scoped precheck (typecheck / build / lint)."""

    kind: str  # typecheck | build | lint
    command: str
    cwd: str  # absolute path
    package_rel: str  # repo-relative package dir ("" = root)
    exit_code: Optional[int] = None
    passed: bool = False
    output_excerpt: str = ""
    error: str = ""
    looks_like_compiler_error: bool = False

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "command": self.command,
            "cwd": self.cwd,
            "package_rel": self.package_rel,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "output_excerpt": self.output_excerpt,
            "error": self.error,
            "looks_like_compiler_error": self.looks_like_compiler_error,
        }


@dataclass
class PackageCheckPlan:
    checks: List[PackageCheck] = field(default_factory=list)
    # Optional package-local test command (cwd, command)
    package_test: Optional[Tuple[str, str]] = None


def find_nearest_package_json(root: Path, rel_file: str) -> Optional[Path]:
    """Walk up from *rel_file* toward *root* looking for package.json."""
    root = Path(root).resolve()
    try:
        start = (root / rel_file).resolve().parent
    except OSError:
        return None
    if not str(start).startswith(str(root)):
        return None
    cur = start
    for _ in range(24):
        candidate = cur / "package.json"
        if candidate.is_file():
            return candidate
        if cur == root:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


def _package_rel(root: Path, pkg_json: Path) -> str:
    try:
        rel = pkg_json.parent.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return ""
    # Workspace root package.json → "" (not "." — callers treat non-empty as nested)
    if rel in ("", "."):
        return ""
    return rel


def _detect_runner(pkg_dir: Path) -> str:
    """Prefer the lockfile-implied package manager."""
    if (pkg_dir / "bun.lockb").is_file() or (pkg_dir / "bun.lock").is_file():
        return "bun"
    # Walk up a few levels for monorepo lockfiles
    cur = pkg_dir
    for _ in range(6):
        if (cur / "bun.lockb").is_file() or (cur / "bun.lock").is_file():
            return "bun"
        if (cur / "pnpm-lock.yaml").is_file():
            return "pnpm"
        if (cur / "yarn.lock").is_file():
            return "yarn"
        if (cur / "package-lock.json").is_file():
            return "npm"
        if cur.parent == cur:
            break
        cur = cur.parent
    return "npm"


def _run_prefix(runner: str) -> str:
    if runner == "bun":
        return "bun run"
    if runner == "pnpm":
        return "pnpm run"
    if runner == "yarn":
        return "yarn"
    return "npm run"


def _pick_script(scripts: dict, names: Sequence[str]) -> Optional[str]:
    for name in names:
        if name in scripts and str(scripts[name]).strip():
            return name
    return None


def _load_scripts(pkg_json: Path) -> dict:
    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = data.get("scripts") or {}
    return scripts if isinstance(scripts, dict) else {}


def _find_test_script_upward(
    root: Path, start_dir: Path
) -> Optional[Tuple[str, str]]:
    """
    Walk from *start_dir* up to *root* for a package.json with a usable ``test``.

    Prefer the nearest nested package that declares ``test``. When only the
    workspace root defines tests (React / Jest monorepos), return that root
    command instead of silence. Skip root scripts that are monorepo guards
    ("do not run npm test from the root").
    """
    root = Path(root).resolve()
    try:
        cur = Path(start_dir).resolve()
    except OSError:
        return None
    if not str(cur).startswith(str(root)):
        cur = root

    root_fallback: Optional[Tuple[str, str]] = None
    for _ in range(24):
        pkg_json = cur / "package.json"
        if pkg_json.is_file():
            scripts = _load_scripts(pkg_json)
            body = str(scripts.get("test") or "").strip()
            if body:
                rel = _package_rel(root, pkg_json)
                runner = _detect_runner(cur)
                prefix = _run_prefix(runner)
                cmd = f"{prefix} test"
                if rel:
                    # Nested package test — best match for this edit
                    return (str(cur.resolve()), cmd)
                # Workspace root: keep as fallback unless it's a "don't run here" guard
                if not looks_like_root_test_guard(body):
                    root_fallback = (str(cur.resolve()), cmd)
        if cur == root:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return root_fallback


def discover_package_checks(
    root: Path,
    edited: Sequence[str],
) -> PackageCheckPlan:
    """
    Build prechecks for each distinct nearest package touched by *edited*.

    Order per package: typecheck → build → lint (first available of each tier).
    Surfaces a ``test`` script from the nearest package that defines one,
    walking up to the workspace root when nested packages omit ``test``
    (common Jest monorepo layout).
    """
    root = Path(root)
    plan = PackageCheckPlan()
    seen_pkgs: Dict[str, Path] = {}

    for rel in edited:
        pkg = find_nearest_package_json(root, rel)
        if not pkg:
            continue
        key = str(pkg.resolve())
        if key not in seen_pkgs:
            seen_pkgs[key] = pkg

    package_test: Optional[Tuple[str, str]] = None

    for pkg_json in seen_pkgs.values():
        scripts = _load_scripts(pkg_json)
        if not scripts:
            # Still try upward test discovery — package.json may exist with no scripts
            if package_test is None:
                package_test = _find_test_script_upward(root, pkg_json.parent)
            continue
        pkg_dir = pkg_json.parent
        rel = _package_rel(root, pkg_json)
        runner = _detect_runner(pkg_dir)
        prefix = _run_prefix(runner)

        for kind, names in (
            ("typecheck", _TYPECHECK_SCRIPT_NAMES),
            ("build", _BUILD_SCRIPT_NAMES),
            ("lint", _LINT_SCRIPT_NAMES),
        ):
            script = _pick_script(scripts, names)
            if not script:
                continue
            # Only one check of the highest-priority kind that exists —
            # typecheck alone is enough when present; otherwise build; else lint.
            plan.checks.append(
                PackageCheck(
                    kind=kind,
                    command=f"{prefix} {script}",
                    cwd=str(pkg_dir.resolve()),
                    package_rel=rel,
                )
            )
            break  # one authoritative precheck per package

        if package_test is None:
            # Nested test if present; else climb to workspace-root test script.
            package_test = _find_test_script_upward(root, pkg_dir)

    # No nearest package.json at all (or none yielded a test) — still try root.
    if package_test is None:
        package_test = _find_test_script_upward(root, root)

    plan.package_test = package_test
    return plan


def looks_like_compiler_output(text: str) -> bool:
    return bool(_COMPILER_ERROR_RE.search(text or ""))


def looks_like_root_test_guard(text: str) -> bool:
    return bool(_ROOT_GUARD_RE.search(text or ""))


def run_package_check(
    check: PackageCheck,
    *,
    verbose: bool = False,
    error_print=None,
) -> PackageCheck:
    """Execute one package check in its package directory."""
    try:
        exit_code, output = run_cmd(
            check.command,
            verbose=verbose,
            error_print=error_print,
            cwd=check.cwd,
        )
    except Exception as err:  # noqa: BLE001
        check.exit_code = 1
        check.passed = False
        check.error = str(err)
        check.output_excerpt = str(err)[-2000:]
        check.looks_like_compiler_error = looks_like_compiler_output(str(err))
        return check

    text = output or ""
    check.exit_code = int(exit_code) if exit_code is not None else 1
    check.output_excerpt = text[-4000:] if len(text) > 4000 else text
    check.passed = check.exit_code == 0
    check.looks_like_compiler_error = (not check.passed) and looks_like_compiler_output(
        text
    )
    if not check.passed and not check.error:
        check.error = f"{check.kind} failed in {check.package_rel or '.'}"
    return check


def run_package_prechecks(
    root: Path,
    edited: Sequence[str],
    *,
    verbose: bool = False,
    error_print=None,
) -> Tuple[List[PackageCheck], Optional[Tuple[str, str]]]:
    """
    Discover and run package prechecks. Stops at the first failing check
    (fail fast — compiler errors should not wait for root npm test).
    """
    plan = discover_package_checks(root, edited)
    results: List[PackageCheck] = []
    for check in plan.checks:
        ran = run_package_check(check, verbose=verbose, error_print=error_print)
        results.append(ran)
        if not ran.passed:
            break
    return results, plan.package_test
