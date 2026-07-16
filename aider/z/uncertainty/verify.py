"""
Real test verification for the Z commit gate.

Runs the project's test suite, treats zero discovered tests as failure,
records a checkable VerificationRecord with structured VerifyState, and
optionally smoke-imports changed Python modules.
"""

from __future__ import annotations

import enum
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from aider.run_cmd import run_cmd

from .detectors import find_relevant_tests


def _python_exe() -> str:
    """Prefer the running interpreter so smoke/tests work when `python` is missing."""
    return sys.executable or "python3"

# Patterns that mean the suite discovered nothing — must not count as pass.
_ZERO_TEST_RE = re.compile(
    r"(?i)("
    r"collected\s+0\s+items?"
    r"|no\s+tests\s+(ran|collected|found)"
    r"|ran\s+0\s+tests?"
    r"|test\s+suite\s+is\s+empty"
    r"|found\s+0\s+tests?"
    r"|0\s+tests?\s+collected"
    r")"
)

_COLLECTED_RE = re.compile(r"(?i)collected\s+(\d+)\s+items?")
_RAN_RE = re.compile(r"(?i)ran\s+(\d+)\s+tests?")
_PASSED_RE = re.compile(r"(?i)(\d+)\s+passed")
_FAILED_RE = re.compile(r"(?i)(\d+)\s+failed")
_ERROR_RE = re.compile(r"(?i)(\d+)\s+errors?")
_COLLECTION_ERR_RE = re.compile(
    r"(?i)(error\s+during\s+collection|collection\s+failed|ERROR\s+collecting)"
)


class VerifyState(str, enum.Enum):
    """Structured verification outcome — never infer from vague substrings alone."""

    NO_TESTS = "NO_TESTS"
    TESTS_PASSED = "TESTS_PASSED"
    TESTS_FAILED = "TESTS_FAILED"
    COLLECTION_FAILED = "COLLECTION_FAILED"
    RUNNER_MISSING = "RUNNER_MISSING"
    TIMED_OUT = "TIMED_OUT"
    NOT_RUN = "NOT_RUN"
    # Package-scoped / compiler failures — distinct from generic test failures
    TYPECHECK_FAILED = "TYPECHECK_FAILED"
    BUILD_FAILED = "BUILD_FAILED"
    LINT_FAILED = "LINT_FAILED"
    TYPE_MEMBER_FAILED = "TYPE_MEMBER_FAILED"


# States that mean "re-read the type / fix the compile error", not "tweak tests"
COMPILER_VERIFY_STATES = frozenset(
    {
        VerifyState.TYPECHECK_FAILED,
        VerifyState.BUILD_FAILED,
        VerifyState.LINT_FAILED,
        VerifyState.TYPE_MEMBER_FAILED,
    }
)


@dataclass
class VerificationRecord:
    """Checkable record of a real verification attempt this session."""

    ran: bool = False
    command: Optional[str] = None
    exit_code: Optional[int] = None
    tests_discovered: Optional[int] = None
    tests_passed: Optional[int] = None
    tests_failed: Optional[int] = None
    collection_errors: int = 0
    zero_tests: bool = False
    passed: bool = False
    state: VerifyState = VerifyState.NOT_RUN
    output_excerpt: str = ""
    smoke_ran: bool = False
    smoke_ok: Optional[bool] = None
    smoke_detail: str = ""
    error: str = ""
    # Package-scoped prechecks (typecheck/build/lint) run before tests
    prechecks: List[dict] = field(default_factory=list)
    failure_kind: str = ""  # test | typecheck | build | lint | type_member | root_guard
    at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value if isinstance(self.state, VerifyState) else str(self.state)
        return d

    @property
    def is_compiler_failure(self) -> bool:
        return self.state in COMPILER_VERIFY_STATES or self.failure_kind in (
            "typecheck",
            "build",
            "lint",
            "type_member",
        )

    @property
    def meaningful_pass(self) -> bool:
        """True only when tests ran, discovered >0, and exited 0."""
        if self.is_compiler_failure:
            return False
        return (
            self.ran
            and self.state == VerifyState.TESTS_PASSED
            and not self.zero_tests
            and (self.tests_discovered or 0) > 0
            and self.exit_code == 0
            and self.passed
        )


def detect_test_command(root: Path) -> Optional[str]:
    """
    Best-effort project test command when --test-cmd is unset.

    Prefer declared runners. For dependency-free Python layouts under tests/,
    use unittest discover (not an undeclared pytest dependency).
    """
    root = Path(root)
    # Explicit pytest config → pytest is declared
    py = _python_exe()
    if (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
        return f"{py} -m pytest -q"
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        if "[tool.pytest" in text:
            return f"{py} -m pytest -q"
        # requirements-style mention alone is weaker — still allow pytest if listed
        if re.search(r"(?m)^\s*pytest\b", text) or '"pytest"' in text or "'pytest'" in text:
            return f"{py} -m pytest -q"
    if (root / "manage.py").is_file():
        return f"{py} manage.py test"
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            if "test" in scripts:
                return "npm test -- --watchAll=false"
        except (OSError, json.JSONDecodeError):
            pass

    # requirements*.txt declaring pytest
    for req in root.glob("requirements*.txt"):
        try:
            rtext = req.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if re.search(r"(?m)^\s*pytest\b", rtext):
            return f"{py} -m pytest -q"

    tests_dir = root / "tests"
    if tests_dir.is_dir() and any(tests_dir.rglob("test_*.py")):
        # Dependency-free default: stdlib unittest with -s tests
        return f"{py} -m unittest discover -s tests -v"
    if any(root.glob("test_*.py")) or any(root.glob("*_test.py")):
        return f"{py} -m unittest discover -v"
    return None


def normalize_test_cmd(test_cmd) -> Optional[str]:
    if not test_cmd:
        return None
    if callable(test_cmd):
        return None  # caller should invoke callable path separately
    if isinstance(test_cmd, (list, tuple)):
        parts = [str(p) for p in test_cmd if p]
        return " ".join(parts) if parts else None
    return str(test_cmd).strip() or None


def parse_counts(output: str) -> dict:
    """Parse discovery / pass / fail / error counts from runner output."""
    text = output or ""
    result = {
        "discovered": None,
        "zero": False,
        "passed": None,
        "failed": None,
        "errors": None,
        "collection_failed": bool(_COLLECTION_ERR_RE.search(text)),
    }
    if _ZERO_TEST_RE.search(text):
        result["discovered"] = 0
        result["zero"] = True
    m = _COLLECTED_RE.search(text)
    if m:
        n = int(m.group(1))
        result["discovered"] = n
        result["zero"] = n == 0
    else:
        m = _RAN_RE.search(text)
        if m:
            n = int(m.group(1))
            result["discovered"] = n
            result["zero"] = n == 0

    passed = _PASSED_RE.search(text)
    failed = _FAILED_RE.search(text)
    errors = _ERROR_RE.search(text)
    if passed:
        result["passed"] = int(passed.group(1))
    if failed:
        result["failed"] = int(failed.group(1))
    if errors:
        result["errors"] = int(errors.group(1))

    # Sum when collected line missing
    if result["discovered"] is None and (passed or failed or errors):
        total = (result["passed"] or 0) + (result["failed"] or 0) + (result["errors"] or 0)
        result["discovered"] = total
        result["zero"] = total == 0
    return result


def parse_discovery_count(output: str) -> Tuple[Optional[int], bool]:
    """
    Return (count, zero_flag).
    zero_flag True when output explicitly indicates zero tests.
    """
    parsed = parse_counts(output)
    return parsed["discovered"], bool(parsed["zero"])


def derive_verify_state(record: "VerificationRecord") -> VerifyState:
    if not record.ran:
        if record.error and "No test command" in (record.error or ""):
            return VerifyState.RUNNER_MISSING
        return VerifyState.NOT_RUN
    if record.error and "timed out" in (record.error or "").lower():
        return VerifyState.TIMED_OUT
    if record.collection_errors or (
        record.tests_discovered is None
        and record.exit_code not in (0, None)
        and "collect" in (record.output_excerpt or "").lower()
    ):
        if record.collection_errors or _COLLECTION_ERR_RE.search(record.output_excerpt or ""):
            return VerifyState.COLLECTION_FAILED
    if record.zero_tests or record.tests_discovered == 0:
        return VerifyState.NO_TESTS
    if (record.tests_discovered or 0) > 0:
        if record.exit_code == 0 and record.passed:
            return VerifyState.TESTS_PASSED
        return VerifyState.TESTS_FAILED
    # Ran but could not confirm discovery — fail closed as NO_TESTS for gate branching
    if record.exit_code == 0:
        return VerifyState.NO_TESTS
    return VerifyState.TESTS_FAILED


def run_test_suite(
    root: Path,
    command: str,
    *,
    verbose: bool = False,
    error_print=None,
    cwd: Optional[Path] = None,
) -> VerificationRecord:
    """Execute the test command and build a VerificationRecord."""
    record = VerificationRecord(ran=True, command=command)
    workdir = str(Path(cwd) if cwd is not None else root)
    try:
        exit_code, output = run_cmd(
            command,
            verbose=verbose,
            error_print=error_print,
            cwd=workdir,
        )
    except Exception as err:  # noqa: BLE001
        record.exit_code = 1
        record.error = str(err)
        record.passed = False
        record.state = (
            VerifyState.TIMED_OUT
            if "timed out" in str(err).lower()
            else VerifyState.TESTS_FAILED
        )
        return record

    record.exit_code = int(exit_code) if exit_code is not None else 1
    text = output or ""
    record.output_excerpt = text[-4000:] if len(text) > 4000 else text
    counts = parse_counts(text)
    record.tests_discovered = counts["discovered"]
    record.tests_passed = counts["passed"]
    record.tests_failed = counts["failed"]
    record.collection_errors = int(counts["errors"] or 0) if counts["collection_failed"] else 0
    if counts["collection_failed"]:
        record.collection_errors = max(record.collection_errors, 1)
    record.zero_tests = bool(counts["zero"]) or (counts["discovered"] == 0)

    if record.zero_tests:
        record.passed = False
    elif counts["discovered"] is None:
        record.passed = False
        record.error = record.error or (
            "Could not confirm tests were discovered from suite output"
        )
    else:
        record.passed = record.exit_code == 0 and counts["discovered"] > 0

    record.state = derive_verify_state(record)
    return record


def path_to_importable(root: Path, rel: str) -> Optional[str]:
    """Convert a .py path under root to a dotted module name when safe."""
    p = Path(rel)
    if p.suffix != ".py":
        return None
    if p.name.startswith("test_") or p.name.endswith("_test.py"):
        return None
    if p.name == "__init__.py":
        parts = list(p.parent.parts)
    else:
        parts = list(p.parent.parts) + [p.stem]
    if any(part.startswith(".") or not part.isidentifier() for part in parts):
        return None
    if not parts:
        return None
    return ".".join(parts)


def run_smoke_cli(
    root: Path,
    *,
    module: str = "",
    args: str = "--help",
    verbose: bool = False,
    error_print=None,
) -> Tuple[bool, str]:
    """
    Process-level CLI smoke via subprocess — asserts exit code, not just import.

    Prefer this over calling main() in-process (which can hide SystemExit bugs).
    """
    root = Path(root)
    mod = module
    if not mod:
        # Heuristic: package __main__ or module with if __name__ among edited later
        return True, "no CLI module specified"
    cmd = f"{_python_exe()} -m {mod} {args}".strip()
    code, out = run_cmd(cmd, verbose=verbose, error_print=error_print, cwd=str(root))
    if code != 0:
        return False, f"{cmd} exit={code}: {(out or '')[-500:]}"
    return True, f"{cmd} exit=0"


def discover_cli_modules(root: Path, edited: Sequence[str]) -> List[str]:
    """Find runnable CLI modules among edited files (__main__.py or *cli*.py)."""
    root = Path(root)
    mods: List[str] = []
    for rel in edited:
        p = Path(rel)
        if p.name == "__main__.py":
            pkg = ".".join(p.parent.parts)
            if pkg and pkg not in mods:
                mods.append(pkg)
        elif p.suffix == ".py" and (
            "cli" in p.stem.lower() or p.stem in ("__main__", "main")
        ):
            if p.name == "main.py" and p.parent == Path("."):
                continue
            parts = list(p.parent.parts) + ([p.stem] if p.stem != "__main__" else [])
            if parts and all(part.isidentifier() for part in parts):
                mod = ".".join(parts)
                if mod not in mods:
                    mods.append(mod)
    return mods[:3]


def run_smoke_imports(
    root: Path,
    edited: Sequence[str],
    *,
    verbose: bool = False,
    error_print=None,
) -> Tuple[bool, str]:
    """
    Basic end-to-end smoke: import changed Python modules.
    Returns (ok, detail). ok True if at least one import attempted and all succeeded,
    or if nothing applicable (vacuous — caller treats as smoke_ran=False).
    """
    root = Path(root)
    modules: List[str] = []
    for rel in edited:
        mod = path_to_importable(root, rel)
        if mod and mod not in modules:
            modules.append(mod)
    modules = modules[:5]
    if not modules:
        return True, "no importable Python modules in edit set"

    failures = []
    for mod in modules:
        # Prefer importing from project root on sys.path
        cmd = (
            f'{_python_exe()} -c "import sys; sys.path.insert(0, \'.\'); import {mod}"'
        )
        code, out = run_cmd(cmd, verbose=verbose, error_print=error_print, cwd=str(root))
        if code != 0:
            failures.append(f"{mod}: {(out or '')[-500:]}")
    if failures:
        return False, "; ".join(failures)
    return True, f"imported: {', '.join(modules)}"


def _state_for_precheck_kind(kind: str) -> VerifyState:
    if kind == "typecheck":
        return VerifyState.TYPECHECK_FAILED
    if kind == "build":
        return VerifyState.BUILD_FAILED
    if kind == "lint":
        return VerifyState.LINT_FAILED
    return VerifyState.TYPECHECK_FAILED


def verify_edits(
    root: Path,
    edited: Sequence[str],
    *,
    test_cmd=None,
    symbols: Sequence[str] = (),
    verbose: bool = False,
    error_print=None,
    skip_smoke: bool = False,
    skip_package_prechecks: bool = False,
    skip_type_members: bool = False,
) -> Tuple[VerificationRecord, List[str]]:
    """
    Full verification pass for a set of edited files.

    Order (fail fast):
      1. Local type-member ground truth (cheap, no subprocess)
      2. Nearest-package typecheck/build/lint
      3. Package-local or root test suite
      4. Smoke imports / CLI

    Returns (record, relevant_test_files).
    """
    root = Path(root)
    relevant = find_relevant_tests(root, edited, symbols)

    # --- 1) Local type-member check (repo ground truth) ---------------------
    if not skip_type_members:
        try:
            from .type_members import check_local_type_members

            tm = check_local_type_members(root, edited)
            if not tm.passed and tm.issues:
                excerpt = "\n".join(i.format() for i in tm.issues[:12])
                record = VerificationRecord(
                    ran=True,
                    command="local-type-member-check",
                    exit_code=1,
                    passed=False,
                    state=VerifyState.TYPE_MEMBER_FAILED,
                    failure_kind="type_member",
                    output_excerpt=excerpt,
                    error=(
                        f"{len(tm.issues)} local type-member mismatch(es) — "
                        "re-read the real type declaration before inventing fields."
                    ),
                    prechecks=[
                        {
                            "kind": "type_member",
                            "passed": False,
                            "issues": len(tm.issues),
                            "types_indexed": tm.types_indexed,
                        }
                    ],
                )
                return record, relevant
        except Exception:  # noqa: BLE001
            pass

    # --- 2) Package-scoped typecheck/build/lint before any tests ------------
    package_test: Optional[Tuple[str, str]] = None
    precheck_dicts: List[dict] = []
    if not skip_package_prechecks:
        try:
            from .package_checks import run_package_prechecks

            results, package_test = run_package_prechecks(
                root, edited, verbose=verbose, error_print=error_print
            )
            precheck_dicts = [c.to_dict() for c in results]
            for check in results:
                if check.passed:
                    continue
                record = VerificationRecord(
                    ran=True,
                    command=check.command,
                    exit_code=check.exit_code,
                    passed=False,
                    state=_state_for_precheck_kind(check.kind),
                    failure_kind=check.kind,
                    output_excerpt=check.output_excerpt,
                    error=check.error
                    or (
                        f"{check.kind} failed in "
                        f"{check.package_rel or '.'} — fix compiler errors before tests."
                    ),
                    prechecks=precheck_dicts,
                )
                return record, relevant
        except Exception:  # noqa: BLE001
            package_test = None

    # --- 3) Test suite (prefer package-local test over root npm test) -------
    cmd = normalize_test_cmd(test_cmd)
    test_cwd: Optional[Path] = None
    if not cmd and package_test:
        test_cwd = Path(package_test[0])
        cmd = package_test[1]
    if not cmd:
        cmd = detect_test_command(root)

    if not cmd:
        record = VerificationRecord(
            ran=False,
            zero_tests=True,
            tests_discovered=0,
            passed=False,
            state=VerifyState.RUNNER_MISSING,
            error="No test command detected and --test-cmd unset",
            prechecks=precheck_dicts,
        )
        return record, relevant

    record = run_test_suite(
        root, cmd, verbose=verbose, error_print=error_print, cwd=test_cwd
    )
    record.prechecks = precheck_dicts
    record.failure_kind = "test"

    # Root monorepo guard: don't treat "don't run npm test from root" as a
    # normal test failure — surface as runner/routing problem.
    from .package_checks import looks_like_compiler_output, looks_like_root_test_guard

    if (
        not record.passed
        and looks_like_root_test_guard(record.output_excerpt or "")
        and not looks_like_compiler_output(record.output_excerpt or "")
    ):
        record.failure_kind = "root_guard"
        record.error = (
            record.error
            or "Root test command looks like a monorepo guard — "
            "run package-local typecheck/tests near the edited files."
        )

    # If suite reported discovery but find_relevant_tests was empty, still use suite count
    if record.tests_discovered is None and relevant and record.exit_code == 0:
        if not record.zero_tests and not _ZERO_TEST_RE.search(record.output_excerpt or ""):
            record.tests_discovered = len(relevant)
            record.passed = True
            record.state = VerifyState.TESTS_PASSED

    if not skip_smoke:
        try:
            ok, detail = run_smoke_imports(
                root, edited, verbose=verbose, error_print=error_print
            )
            cli_mods = discover_cli_modules(root, edited)
            cli_details = []
            cli_ok = True
            for mod in cli_mods:
                cok, cdetail = run_smoke_cli(
                    root, module=mod, args="--help", verbose=verbose, error_print=error_print
                )
                cli_details.append(cdetail)
                if not cok:
                    cli_ok = False
            # Prefer CLI process smoke when available; else import smoke
            if cli_mods:
                record.smoke_ran = True
                record.smoke_ok = cli_ok and (ok if not detail.startswith("no importable") else True)
                record.smoke_detail = "; ".join(cli_details + ([detail] if not detail.startswith("no importable") else []))
                if not record.smoke_ok:
                    record.passed = False
                    if record.state == VerifyState.TESTS_PASSED:
                        record.state = VerifyState.TESTS_FAILED
                    if not record.error:
                        record.error = f"Smoke CLI failed: {record.smoke_detail}"
            elif detail.startswith("no importable"):
                record.smoke_ran = False
                record.smoke_ok = None
                record.smoke_detail = detail
            else:
                record.smoke_ran = True
                record.smoke_ok = ok
                record.smoke_detail = detail
                if not ok:
                    record.passed = False
                    if record.state == VerifyState.TESTS_PASSED:
                        record.state = VerifyState.TESTS_FAILED
                    if not record.error:
                        record.error = f"Smoke import failed: {detail}"
        except Exception as err:  # noqa: BLE001
            record.smoke_ran = True
            record.smoke_ok = False
            record.smoke_detail = str(err)

    # Re-derive after smoke adjustments
    if record.ran and record.state not in (VerifyState.RUNNER_MISSING, VerifyState.TIMED_OUT):
        record.state = derive_verify_state(record)

    return record, relevant


def gate_enabled() -> bool:
    """Env escape hatch for CI / nested test runs."""
    return os.environ.get("Z_SKIP_VERIFY_GATE", "").strip() not in ("1", "true", "yes")
