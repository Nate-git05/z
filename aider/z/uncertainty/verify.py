"""
Real test verification for the Z commit gate.

Runs the project's test suite, treats zero discovered tests as failure,
records a checkable VerificationRecord, and optionally smoke-imports
changed Python modules.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from aider.run_cmd import run_cmd

from .detectors import find_relevant_tests

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


@dataclass
class VerificationRecord:
    """Checkable record of a real verification attempt this session."""

    ran: bool = False
    command: Optional[str] = None
    exit_code: Optional[int] = None
    tests_discovered: Optional[int] = None
    zero_tests: bool = False
    passed: bool = False
    output_excerpt: str = ""
    smoke_ran: bool = False
    smoke_ok: Optional[bool] = None
    smoke_detail: str = ""
    error: str = ""
    at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def meaningful_pass(self) -> bool:
        """True only when tests ran, discovered >0, and exited 0."""
        return (
            self.ran
            and not self.zero_tests
            and (self.tests_discovered or 0) > 0
            and self.exit_code == 0
            and self.passed
        )


def detect_test_command(root: Path) -> Optional[str]:
    """Best-effort project test command when --test-cmd is unset."""
    root = Path(root)
    if (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
        return "python -m pytest -q"
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        if "[tool.pytest" in text or "pytest" in text:
            return "python -m pytest -q"
    if (root / "manage.py").is_file():
        return "python manage.py test"
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            if "test" in scripts:
                return "npm test -- --watchAll=false"
        except (OSError, json.JSONDecodeError):
            pass
    tests_dir = root / "tests"
    if tests_dir.is_dir() and any(tests_dir.rglob("test_*.py")):
        return "python -m pytest -q"
    if any(root.glob("test_*.py")) or any(root.glob("*_test.py")):
        return "python -m pytest -q"
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


def parse_discovery_count(output: str) -> Tuple[Optional[int], bool]:
    """
    Return (count, zero_flag).
    zero_flag True when output explicitly indicates zero tests.
    """
    text = output or ""
    if _ZERO_TEST_RE.search(text):
        return 0, True
    m = _COLLECTED_RE.search(text)
    if m:
        n = int(m.group(1))
        return n, n == 0
    m = _RAN_RE.search(text)
    if m:
        n = int(m.group(1))
        return n, n == 0
    # Fall back to passed+failed+error sums when present
    passed = _PASSED_RE.search(text)
    failed = _FAILED_RE.search(text)
    errors = _ERROR_RE.search(text)
    if passed or failed or errors:
        total = 0
        if passed:
            total += int(passed.group(1))
        if failed:
            total += int(failed.group(1))
        if errors:
            total += int(errors.group(1))
        return total, total == 0
    return None, False


def run_test_suite(
    root: Path,
    command: str,
    *,
    verbose: bool = False,
    error_print=None,
) -> VerificationRecord:
    """Execute the test command and build a VerificationRecord."""
    record = VerificationRecord(ran=True, command=command)
    try:
        exit_code, output = run_cmd(
            command,
            verbose=verbose,
            error_print=error_print,
            cwd=str(root),
        )
    except Exception as err:  # noqa: BLE001
        record.exit_code = 1
        record.error = str(err)
        record.passed = False
        return record

    record.exit_code = int(exit_code) if exit_code is not None else 1
    text = output or ""
    record.output_excerpt = text[-4000:] if len(text) > 4000 else text
    count, zero = parse_discovery_count(text)
    record.tests_discovered = count
    record.zero_tests = zero or (count == 0)
    # Unknown discovery with exit 0: do not treat as meaningful pass
    if record.zero_tests:
        record.passed = False
    elif count is None:
        # Could not confirm discovery — fail closed for the gate
        record.passed = False
        record.error = record.error or "Could not confirm tests were discovered from suite output"
    else:
        record.passed = record.exit_code == 0 and count > 0
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
        cmd = f'python -c "import sys; sys.path.insert(0, \'.\'); import {mod}"'
        code, out = run_cmd(cmd, verbose=verbose, error_print=error_print, cwd=str(root))
        if code != 0:
            failures.append(f"{mod}: {(out or '')[-500:]}")
    if failures:
        return False, "; ".join(failures)
    return True, f"imported: {', '.join(modules)}"


def verify_edits(
    root: Path,
    edited: Sequence[str],
    *,
    test_cmd=None,
    symbols: Sequence[str] = (),
    verbose: bool = False,
    error_print=None,
    skip_smoke: bool = False,
) -> Tuple[VerificationRecord, List[str]]:
    """
    Full verification pass for a set of edited files.

    Returns (record, relevant_test_files).
    """
    root = Path(root)
    relevant = find_relevant_tests(root, edited, symbols)
    cmd = normalize_test_cmd(test_cmd) or detect_test_command(root)

    if not cmd:
        record = VerificationRecord(
            ran=False,
            zero_tests=True,
            tests_discovered=0,
            passed=False,
            error="No test command detected and --test-cmd unset",
        )
        return record, relevant

    record = run_test_suite(root, cmd, verbose=verbose, error_print=error_print)

    # If suite reported discovery but find_relevant_tests was empty, still use suite count
    if record.tests_discovered is None and relevant and record.exit_code == 0:
        # Some quiet runners omit collection lines — if we know relevant tests exist
        # and exit was 0, accept len(relevant) as discovery lower bound only when
        # output does not explicitly say zero.
        if not record.zero_tests and not _ZERO_TEST_RE.search(record.output_excerpt or ""):
            record.tests_discovered = len(relevant)
            record.passed = True

    if not skip_smoke:
        # Only smoke when primary suite looks usable or we want extra signal
        try:
            ok, detail = run_smoke_imports(
                root, edited, verbose=verbose, error_print=error_print
            )
            # Vacuous (no modules) → smoke_ran False
            if detail.startswith("no importable"):
                record.smoke_ran = False
                record.smoke_ok = None
                record.smoke_detail = detail
            else:
                record.smoke_ran = True
                record.smoke_ok = ok
                record.smoke_detail = detail
                if not ok:
                    record.passed = False
                    if not record.error:
                        record.error = f"Smoke import failed: {detail}"
        except Exception as err:  # noqa: BLE001
            record.smoke_ran = True
            record.smoke_ok = False
            record.smoke_detail = str(err)

    return record, relevant


def gate_enabled() -> bool:
    """Env escape hatch for CI / nested test runs."""
    return os.environ.get("Z_SKIP_VERIFY_GATE", "").strip() not in ("1", "true", "yes")
