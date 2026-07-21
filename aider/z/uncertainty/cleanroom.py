"""Clean-room completion verification.

Before claiming production readiness:
  Remove generated deps/build output (optional)
  → Install from lockfile
  → Typecheck
  → Lint
  → Unit tests
  → Integration tests (when declared)
  → Build production artifact
  → Start production artifact
  → Smoke / E2E against the built app

The install step itself is part of verification. Each step writes an
EvidenceRecord; later edits invalidate the ledger.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from aider.run_cmd import run_cmd

from .evidence import EvidenceLedger, EvidenceRecord, tree_hash


@dataclass
class CleanRoomStep:
    kind: str
    command: str
    cwd: str
    required: bool = True
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CleanRoomPlan:
    steps: List[CleanRoomStep] = field(default_factory=list)
    package_rel: str = ""

    def to_dict(self) -> dict:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "package_rel": self.package_rel,
        }


@dataclass
class CleanRoomResult:
    plan: CleanRoomPlan
    records: List[EvidenceRecord] = field(default_factory=list)
    passed: bool = False
    failed_step: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "plan": self.plan.to_dict(),
            "records": [r.to_dict() for r in self.records],
            "passed": self.passed,
            "failed_step": self.failed_step,
            "detail": self.detail,
        }

    @property
    def clean_install_ok(self) -> Optional[bool]:
        return _status(self.records, "clean_install")

    @property
    def production_build_ok(self) -> Optional[bool]:
        return _status(self.records, "build")

    @property
    def production_start_ok(self) -> Optional[bool]:
        return _status(self.records, "start")

    @property
    def smoke_ok(self) -> Optional[bool]:
        return _status(self.records, "smoke")


def _status(records: Sequence[EvidenceRecord], kind: str) -> Optional[bool]:
    for r in reversed(list(records)):
        if r.kind == kind:
            return bool(r.passed and not r.stale)
    return None


def _load_scripts(pkg: Path) -> dict:
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = data.get("scripts") or {}
    return scripts if isinstance(scripts, dict) else {}


def _pick(scripts: dict, names: Sequence[str]) -> Optional[str]:
    for n in names:
        if n in scripts and str(scripts[n]).strip():
            return n
    return None


def _detect_install_cmd(pkg_dir: Path) -> Optional[str]:
    if (pkg_dir / "bun.lockb").is_file() or (pkg_dir / "bun.lock").is_file():
        return "bun install --frozen-lockfile"
    if (pkg_dir / "pnpm-lock.yaml").is_file():
        return "pnpm install --frozen-lockfile"
    if (pkg_dir / "yarn.lock").is_file():
        return "yarn install --frozen-lockfile"
    if (pkg_dir / "package-lock.json").is_file():
        return "npm ci"
    if (pkg_dir / "package.json").is_file():
        return "npm install"
    # Python
    if (pkg_dir / "requirements.txt").is_file():
        return "python -m pip install -r requirements.txt"
    if (pkg_dir / "pyproject.toml").is_file():
        return "python -m pip install -e ."
    return None


def _find_package_dir(root: Path, edited: Sequence[str] = ()) -> Path:
    root = Path(root).resolve()
    from .package_checks import find_nearest_package_json

    for rel in edited:
        found = find_nearest_package_json(root, rel)
        if found:
            return found.parent
    if (root / "package.json").is_file():
        return root
    # nested web/
    for candidate in (root / "web", root / "frontend", root / "app"):
        if (candidate / "package.json").is_file():
            return candidate
    return root


def _runner_prefix(pkg_dir: Path) -> str:
    if (pkg_dir / "bun.lockb").is_file() or (pkg_dir / "bun.lock").is_file():
        return "bun run"
    if (pkg_dir / "pnpm-lock.yaml").is_file():
        return "pnpm run"
    if (pkg_dir / "yarn.lock").is_file():
        return "yarn"
    return "npm run"


def discover_cleanroom_plan(
    root: Path,
    *,
    edited: Sequence[str] = (),
    include_wipe: bool = False,
) -> CleanRoomPlan:
    """Build the clean-room step list without executing."""
    root = Path(root)
    pkg_dir = _find_package_dir(root, edited)
    try:
        package_rel = str(pkg_dir.relative_to(root.resolve())).replace("\\", "/")
        if package_rel == ".":
            package_rel = ""
    except ValueError:
        package_rel = ""

    steps: List[CleanRoomStep] = []
    cwd = str(pkg_dir)

    if include_wipe:
        steps.append(
            CleanRoomStep(
                kind="wipe_generated",
                command="__wipe__",  # handled specially
                cwd=cwd,
                required=False,
                skip_reason="optional wipe of node_modules/dist/.next",
            )
        )

    install = _detect_install_cmd(pkg_dir)
    if install:
        steps.append(
            CleanRoomStep(kind="clean_install", command=install, cwd=cwd, required=True)
        )
    else:
        steps.append(
            CleanRoomStep(
                kind="clean_install",
                command="",
                cwd=cwd,
                required=False,
                skip_reason="no lockfile/manifest install command detected",
            )
        )

    scripts = _load_scripts(pkg_dir / "package.json") if (pkg_dir / "package.json").is_file() else {}
    prefix = _runner_prefix(pkg_dir)

    for kind, names in (
        ("typecheck", ("typecheck", "type-check", "types", "tsc")),
        ("lint", ("lint", "eslint")),
        ("unit", ("test", "test:unit")),
        ("integration", ("test:integration", "test:api")),
        ("build", ("build", "compile")),
    ):
        script = _pick(scripts, names)
        if script:
            cmd = f"{prefix} {script}" if prefix != "yarn" else f"yarn {script}"
            if kind == "unit" and "npm" in prefix:
                cmd = "npm test -- --watchAll=false"
            steps.append(CleanRoomStep(kind=kind, command=cmd, cwd=cwd, required=kind != "integration"))
        elif kind in ("typecheck", "lint", "build", "unit"):
            # Python fallbacks
            if kind == "unit" and (
                (pkg_dir / "pytest.ini").is_file()
                or (pkg_dir / "tests").is_dir()
                or (root / "tests").is_dir()
            ):
                steps.append(
                    CleanRoomStep(
                        kind="unit",
                        command="python -m pytest -q",
                        cwd=str(root),
                        required=True,
                    )
                )
            elif kind == "typecheck" and (pkg_dir / "mypy.ini").is_file():
                steps.append(
                    CleanRoomStep(
                        kind="typecheck",
                        command="python -m mypy .",
                        cwd=cwd,
                        required=False,
                    )
                )
            else:
                steps.append(
                    CleanRoomStep(
                        kind=kind,
                        command="",
                        cwd=cwd,
                        required=False,
                        skip_reason=f"no {kind} script/manifest detected",
                    )
                )

    # Start + smoke when build exists
    start_script = _pick(scripts, ("start", "start:prod", "serve"))
    if start_script:
        cmd = f"{prefix} {start_script}" if prefix != "yarn" else f"yarn {start_script}"
        steps.append(CleanRoomStep(kind="start", command=cmd, cwd=cwd, required=False))
        steps.append(
            CleanRoomStep(
                kind="smoke",
                command="__smoke_http__",
                cwd=cwd,
                required=False,
                skip_reason="HTTP smoke against started app when possible",
            )
        )
    else:
        steps.append(
            CleanRoomStep(
                kind="start",
                command="",
                cwd=cwd,
                required=False,
                skip_reason="no start script",
            )
        )
        steps.append(
            CleanRoomStep(
                kind="smoke",
                command="",
                cwd=cwd,
                required=False,
                skip_reason="no start script for smoke",
            )
        )

    return CleanRoomPlan(steps=steps, package_rel=package_rel)


def _wipe_generated(pkg_dir: Path) -> Tuple[bool, str]:
    removed = []
    for name in ("node_modules", "dist", "build", ".next", "out", "coverage"):
        target = pkg_dir / name
        if target.is_dir():
            try:
                shutil.rmtree(target)
                removed.append(name)
            except OSError as err:
                return False, f"failed to remove {name}: {err}"
    return True, f"removed: {', '.join(removed) or '(nothing present)'}"


def _smoke_http(cwd: str, verbose: bool = False) -> Tuple[bool, str, str]:
    """Best-effort GET against localhost common ports — does not start the server."""
    # We only probe; start is a separate step. If nothing listens, report unverified.
    import urllib.error
    import urllib.request

    for port in (3000, 8000, 8080, 4173, 5000):
        url = f"http://127.0.0.1:{port}/"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                code = getattr(resp, "status", 200)
                if 200 <= int(code) < 500:
                    return True, f"GET {url} → {code}", f"curl -s -o /dev/null -w '%{{http_code}}' {url}"
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
    return False, "no listening app on common ports (start the built app first)", "__smoke_http__"


def run_cleanroom(
    root: Path,
    *,
    edited: Sequence[str] = (),
    ledger: Optional[EvidenceLedger] = None,
    include_wipe: bool = False,
    verbose: bool = False,
    error_print=None,
    dry_run: bool = False,
    max_steps: int = 12,
) -> CleanRoomResult:
    """
    Execute clean-room plan. Set dry_run=True to only discover the plan.

    Env ``Z_SKIP_CLEANROOM=1`` skips execution (plan still returned).
    """
    root = Path(root)
    plan = discover_cleanroom_plan(root, edited=edited, include_wipe=include_wipe)
    result = CleanRoomResult(plan=plan)
    ledger = ledger if ledger is not None else EvidenceLedger()

    if dry_run or os.environ.get("Z_SKIP_CLEANROOM", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        result.detail = "clean-room dry-run / skipped by env"
        result.passed = False
        return result

    th = tree_hash(root, paths=list(edited) if edited else ())
    executed = 0
    for step in plan.steps:
        if executed >= max_steps:
            break
        if not step.command and step.skip_reason:
            continue
        if step.kind == "wipe_generated":
            ok, detail = _wipe_generated(Path(step.cwd))
            rec = EvidenceRecord(
                kind=step.kind,
                command="wipe node_modules/dist/.next",
                cwd=step.cwd,
                exit_code=0 if ok else 1,
                output_excerpt=detail,
                passed=ok,
                tree_hash_at_run=th,
                evidence_type="execution",
            )
            ledger.add(rec)
            result.records.append(rec)
            executed += 1
            if not ok and step.required:
                result.failed_step = step.kind
                result.detail = detail
                return result
            continue

        if step.kind == "smoke" and step.command == "__smoke_http__":
            ok, detail, cmd = _smoke_http(step.cwd, verbose=verbose)
            rec = EvidenceRecord(
                kind="smoke",
                command=cmd,
                cwd=step.cwd,
                exit_code=0 if ok else 1,
                output_excerpt=detail,
                passed=ok,
                tree_hash_at_run=tree_hash(root, paths=list(edited) if edited else ()),
                evidence_type="production_build",
                env_assumptions=["app listening on localhost"],
            )
            ledger.add(rec)
            result.records.append(rec)
            executed += 1
            # smoke is non-required by default
            continue

        if not step.command:
            continue

        code, out = run_cmd(
            step.command,
            verbose=verbose,
            error_print=error_print,
            cwd=step.cwd,
        )
        ok = code == 0
        th = tree_hash(root, paths=list(edited) if edited else ())
        rec = EvidenceRecord(
            kind=step.kind,
            command=step.command,
            cwd=step.cwd,
            exit_code=code,
            output_excerpt=(out or "")[-3000:],
            passed=ok,
            tree_hash_at_run=th,
            evidence_type="execution" if step.kind == "clean_install" else step.kind,
            env_assumptions=[f"cwd={step.cwd}"],
        )
        ledger.add(rec)
        result.records.append(rec)
        executed += 1
        if not ok and step.required:
            result.failed_step = step.kind
            result.detail = (out or "")[-800:]
            return result

    # Passed if all required steps that ran are green
    required_kinds = {s.kind for s in plan.steps if s.required and s.command}
    for kind in required_kinds:
        if not any(r.kind == kind and r.passed and not r.stale for r in result.records):
            # required step was skipped entirely
            if any(s.kind == kind and s.command for s in plan.steps):
                result.failed_step = kind
                result.detail = f"required step {kind} did not pass"
                result.passed = False
                return result
    result.passed = True
    result.detail = f"clean-room ok ({len(result.records)} steps)"
    return result


def format_cleanroom_result(result: CleanRoomResult) -> str:
    lines = ["Clean-room verification:", ""]
    for s in result.plan.steps:
        rec = next((r for r in result.records if r.kind == s.kind), None)
        if rec:
            mark = "[x]" if rec.passed and not rec.stale else "[ ]"
            lines.append(f"  {mark} {s.kind}: {s.command or s.skip_reason} (exit={rec.exit_code})")
        elif s.skip_reason and not s.command:
            lines.append(f"  [-] {s.kind}: skipped — {s.skip_reason}")
        else:
            lines.append(f"  [ ] {s.kind}: {s.command or '(pending)'}")
    lines.append("")
    lines.append(f"Status: {'PASSED' if result.passed else 'FAILED / INCOMPLETE'}")
    if result.failed_step:
        lines.append(f"Failed step: {result.failed_step}")
    if result.detail:
        lines.append(result.detail[:500])
    return "\n".join(lines)
