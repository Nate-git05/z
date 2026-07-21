"""Verification integrity — never weaken checks to go green.

Protected surfaces (package scripts, CI, lint/tsconfig strictness, test
assertions, coverage thresholds, git hooks) may be *repaired* when broken,
but their strength must not be reduced after a failure without explicit
human approval.

Invariant:
  A failing verification command may be repaired,
  but its strength may not be reduced without explicit human approval.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence, Set

from .schema import (
    Area,
    NodeStatus,
    NodeType,
    Tier,
    UncertaintyNode,
)


# Paths / globs that constitute the verification mechanism itself
_PROTECTED_PATH_RE = re.compile(
    r"(?i)("
    r"(^|/)package\.json$"
    r"|(^|/)package-lock\.json$"
    r"|(^|/)pnpm-lock\.yaml$"
    r"|(^|/)yarn\.lock$"
    r"|(^|/)bun\.lockb?$"
    r"|(^|/)tsconfig(?:\.[^/]+)?\.json$"
    r"|(^|/)jsconfig\.json$"
    r"|(^|/)\.eslintrc(?:\.\w+)?$"
    r"|(^|/)eslint\.config\.\w+$"
    r"|(^|/)\.prettierrc(?:\.\w+)?$"
    r"|(^|/)pyproject\.toml$"
    r"|(^|/)pytest\.ini$"
    r"|(^|/)setup\.cfg$"
    r"|(^|/)tox\.ini$"
    r"|(^|/)mypy\.ini$"
    r"|(^|/)\.github/workflows/[^/]+\.ya?ml$"
    r"|(^|/)\.gitlab-ci\.yml$"
    r"|(^|/)\.pre-commit-config\.yaml$"
    r"|(^|/)\.husky/"
    r"|(^|/)Makefile$"
    r"|(^|/)Justfile$"
    r")"
)

# Diff patterns that weaken verification strength
_WEAKENING_PATTERNS: Sequence[tuple[str, re.Pattern[str]]] = (
    (
        "script_noop",
        re.compile(
            r"(?m)^\+.*['\"](?:typecheck|type-check|types|test|lint|build|ci)"
            r"['\"]\s*:\s*['\"](?:true|exit\s*0|echo\s+ok|:|true\b)['\"]"
        ),
    ),
    (
        "script_removed",
        re.compile(
            r"(?m)^-.*['\"](?:typecheck|type-check|types|test|lint|build)['\"]\s*:"
        ),
    ),
    (
        "strict_disabled",
        re.compile(
            r"(?m)^\+\s*['\"]?(?:strict|noImplicitAny|strictNullChecks|"
            r"noUnusedLocals|exactOptionalPropertyTypes)['\"]?\s*[=:]\s*"
            r"(?:false|False|0)"
        ),
    ),
    (
        "ts_ignore_added",
        re.compile(r"(?m)^\+\s*(?://\s*)?@ts-ignore\b|^\+\s*//\s*@ts-nocheck\b"),
    ),
    (
        "eslint_disable_global",
        re.compile(
            r"(?m)^\+\s*(?://\s*)?eslint-disable(?!-next-line)\b"
            r"|^\+\s*/\*\s*eslint-disable\b"
        ),
    ),
    (
        "test_skipped",
        re.compile(
            r"(?m)^\+\s*(?:it|test|describe)\.skip\s*\("
            r"|^\+\s*@unittest\.skip"
            r"|^\+\s*pytest\.mark\.skip"
            r"|^\+.*\bxfail\b.*\balways\b"
        ),
    ),
    (
        "assertion_weakened",
        re.compile(
            r"(?m)^\+.*(?:toBeTruthy|toBeFalsy|toBeDefined|toBeTruthy\(\))"
            r"|^\+.*expect\([^)]*(?:\|\||or)[^)]*\)\.toBe"
            r"|^\+.*assertTrue\s*\(\s*.*(?:\|\||or)"
        ),
    ),
    (
        "ci_ignore_exit",
        re.compile(
            r"(?m)^\+.*continue-on-error\s*:\s*true"
            r"|^\+.*\|\|\s*true\s*$"
            r"|^\+.*allow_failure\s*:\s*true"
        ),
    ),
    (
        "coverage_lowered",
        re.compile(
            r"(?m)^\+.*(?:coverageThreshold|fail_under|min_coverage).*"
            r"(?:0|1|5|10)\b"
        ),
    ),
)


@dataclass
class IntegrityFinding:
    """One proposed edit that appears to weaken verification."""

    kind: str
    path: str
    detail: str
    blocked: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IntegrityReport:
    """Result of scanning a diff against protected verification surfaces."""

    findings: List[IntegrityFinding] = field(default_factory=list)
    protected_paths_touched: List[str] = field(default_factory=list)
    had_prior_failure: bool = False
    approved_override: bool = False

    @property
    def blocked(self) -> bool:
        if self.approved_override:
            return False
        return any(f.blocked for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "protected_paths_touched": list(self.protected_paths_touched),
            "had_prior_failure": self.had_prior_failure,
            "approved_override": self.approved_override,
            "blocked": self.blocked,
        }


def is_protected_path(path: str) -> bool:
    rel = (path or "").replace("\\", "/").lstrip("./")
    return bool(_PROTECTED_PATH_RE.search(rel))


def _files_in_diff(diff: str) -> Set[str]:
    files: Set[str] = set()
    for m in re.finditer(r"(?m)^(?:\+\+\+|---) [ab]/(.+)$", diff or ""):
        p = m.group(1).strip()
        if p != "/dev/null":
            files.add(p.lstrip("./"))
    for m in re.finditer(r"(?m)^diff --git a/(.+?) b/(.+)$", diff or ""):
        files.add(m.group(1).lstrip("./"))
        files.add(m.group(2).lstrip("./"))
    return files


def scan_verification_integrity(
    diff: str,
    *,
    edited: Sequence[str] = (),
    had_prior_failure: bool = False,
    approved_override: bool = False,
) -> IntegrityReport:
    """
    Scan a unified diff for verification-weakening edits.

    When ``had_prior_failure`` is True, any weakening pattern on a protected
    surface (or assertion-skip patterns in tests) is a hard block.
    """
    report = IntegrityReport(
        had_prior_failure=had_prior_failure,
        approved_override=approved_override,
    )
    if not diff and not edited:
        return report

    touched = set(_files_in_diff(diff)) if diff else set()
    for p in edited:
        if p:
            touched.add(str(p).replace("\\", "/").lstrip("./"))

    protected = sorted(p for p in touched if is_protected_path(p))
    report.protected_paths_touched = protected

    blob = diff or ""
    for kind, pattern in _WEAKENING_PATTERNS:
        if not pattern.search(blob):
            continue
        # Assertion/skip weakenings always matter; config weakenings matter
        # especially after a prior failure or when protected paths are touched.
        block = True
        if kind in ("assertion_weakened", "test_skipped", "ts_ignore_added"):
            block = True
        elif not protected and not had_prior_failure:
            # Touching only product code with a coincidental pattern — still
            # flag but only hard-block after a prior failure.
            block = False
        path = protected[0] if protected else (sorted(touched)[0] if touched else "")
        report.findings.append(
            IntegrityFinding(
                kind=kind,
                path=path,
                detail=(
                    f"Diff matches verification-weakening pattern '{kind}'. "
                    "A failing check may be repaired, but its strength must "
                    "not be reduced without human approval."
                ),
                blocked=block and not approved_override,
            )
        )

    # Touching protected surfaces after a failure with no clear strengthening
    # still raises scrutiny even without a specific pattern match.
    if had_prior_failure and protected and not report.findings:
        report.findings.append(
            IntegrityFinding(
                kind="protected_surface_after_failure",
                path=protected[0],
                detail=(
                    "Prior verification failed and this edit touches a protected "
                    f"verification surface ({', '.join(protected[:4])}). "
                    "Require proof that verification strength is preserved."
                ),
                blocked=False,  # scrutiny node, not automatic hard block
            )
        )

    return report


def integrity_nodes_from_report(
    report: IntegrityReport,
    *,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
) -> List[UncertaintyNode]:
    """Raise High uncertainty nodes for blocked integrity findings."""
    nodes: List[UncertaintyNode] = []
    if not report.findings:
        return nodes
    blocked = [f for f in report.findings if f.blocked]
    if not blocked:
        # Soft scrutiny for protected-surface-after-failure
        for f in report.findings:
            nodes.append(
                UncertaintyNode(
                    title=f"Verification integrity scrutiny — {f.kind}",
                    type=NodeType.VERIFICATION_INTEGRITY,
                    confidence_tier=Tier.LOW,
                    risk_tier=Tier.MEDIUM,
                    summary=f.detail,
                    explanation=(
                        f"Path: {f.path or '(unknown)'}\n"
                        "Detector failed → proposed edit touches detector → "
                        "require proof strength is preserved."
                    ),
                    files_affected=[f.path] if f.path else [],
                    why_uncertain=(
                        "Conflict of interest: the edit may weaken the mechanism "
                        "that detected the failure."
                    ),
                    what_could_go_wrong=(
                        "False green: verification appears to pass because the "
                        "check was weakened, not because the product works."
                    ),
                    suggested_fix=(
                        "Revert verification-config changes; fix the product or "
                        "environment instead. Seek human approval only if a "
                        "legitimate config change is required."
                    ),
                    status=NodeStatus.OPEN,
                    area=Area.CONFIG,
                    task_id=task_id,
                    task_title=task_title,
                    created_by_session=created_by_session,
                    signals={
                        "verification_integrity": True,
                        "integrity_kind": f.kind,
                        "had_prior_failure": report.had_prior_failure,
                    },
                )
            )
        return nodes

    kinds = ", ".join(sorted({f.kind for f in blocked}))
    paths = sorted({f.path for f in blocked if f.path})
    nodes.append(
        UncertaintyNode(
            title="Verification integrity violation — check weakening blocked",
            type=NodeType.VERIFICATION_INTEGRITY,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.HIGH,
            summary=(
                f"Blocked automatic weakening of verification ({kinds}). "
                "Preserve check strength; fix the underlying failure."
            ),
            explanation="\n".join(
                f"- [{f.kind}] {f.path}: {f.detail}" for f in blocked
            ),
            files_affected=paths,
            why_uncertain=(
                "Proposed fix touches the failing detector in a way that "
                "reduces verification strength."
            ),
            what_could_go_wrong=(
                "False completion: the suite goes green because checks were "
                "disabled/weakened while the product remains broken."
            ),
            suggested_fix=(
                "Restore the original typecheck/test/lint/CI configuration. "
                "Classify the failure layer (env vs type vs assertion) and "
                "repair the earliest unsupported assumption. Do not replace "
                "typecheck with `exit 0`, remove failing tests, add broad "
                "@ts-ignore, or disable strict mode without human approval."
            ),
            suggested_prompt=(
                "VERIFICATION INTEGRITY BLOCK\n"
                "Your previous edit appears to weaken a failing verification "
                "mechanism. That is forbidden without human approval.\n"
                "Revert the weakening. Fix the product code, install real "
                "dependencies, or report an environmental blockage.\n"
                f"Findings:\n"
                + "\n".join(f"- {f.kind} @ {f.path}" for f in blocked)
            ),
            status=NodeStatus.OPEN,
            area=Area.CONFIG,
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            signals={
                "verification_integrity": True,
                "verification_blocked": True,
                "integrity_kinds": [f.kind for f in blocked],
                "had_prior_failure": report.had_prior_failure,
            },
        )
    )
    return nodes


def format_integrity_block(report: IntegrityReport) -> str:
    if not report.blocked:
        return ""
    lines = [
        "Z verification integrity: BLOCKED",
        "A failing verification command may be repaired, but its strength may "
        "not be reduced without explicit human approval.",
        "",
        "Findings:",
    ]
    for f in report.findings:
        if f.blocked:
            lines.append(f"  - [{f.kind}] {f.path or '(path unknown)'}: {f.detail}")
    lines.append("")
    lines.append(
        "REQUIRED: revert the weakening, classify the failure layer, and repair "
        "the earliest unsupported assumption. Then re-run the original check "
        "unchanged."
    )
    return "\n".join(lines)
