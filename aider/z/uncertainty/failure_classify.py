"""Root-cause failure classification for verification commands.

Separates environment/dependency failures from product/compiler/test
failures so the agent backtracks to the earliest unsupported assumption
instead of treating ``tsc: command not found`` as a TypeScript error.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional, Sequence


# Ordered: first match wins. More specific patterns before broader ones.
_LAYER_PATTERNS: Sequence[tuple[str, re.Pattern[str], str]] = (
    (
        "command_not_found",
        re.compile(
            r"(?i)("
            r"command\s+not\s+found"
            r"|not\s+recognized\s+as\s+(?:an\s+internal|a\s+command)"
            r"|no\s+such\s+file\s+or\s+directory.*(tsc|node|npm|bun|pnpm|yarn|pytest|mypy|eslint)"
            r"|(?:tsc|npx|npm|bun|pnpm|yarn|pytest|mypy|eslint|go|cargo):\s*"
            r"(?:command\s+not\s+found|not\s+found)"
            r"|Cannot\s+find\s+module\s+'typescript'"
            r")"
        ),
        "Executable or toolchain missing — prepare the environment / install deps.",
    ),
    (
        "dependency_install",
        re.compile(
            r"(?i)("
            r"ERESOLVE|npm\s+ERR!"
            r"|Could\s+not\s+find\s+a\s+version\s+that\s+satisfies"
            r"|No\s+matching\s+distribution\s+found"
            r"|version\s+.*\s+is\s+not\s+(?:available|found)"
            r"|Package\s+['\"][^'\"]+['\"]\s+not\s+found"
            r"|Unable\s+to\s+resolve\s+dependency"
            r"|lockfile.*out\s+of\s+date"
            r")"
        ),
        "Package/manifest/version problem — correct using registry evidence.",
    ),
    (
        "permission",
        re.compile(
            r"(?i)("
            r"permission\s+denied"
            r"|EACCES"
            r"|operation\s+not\s+permitted"
            r")"
        ),
        "Permission failure — request access or use a supported environment.",
    ),
    (
        "network",
        re.compile(
            r"(?i)("
            r"ECONNREFUSED|ENOTFOUND|ETIMEDOUT"
            r"|network\s+(?:is\s+)?unreachable"
            r"|Could\s+not\s+resolve\s+host"
            r"|TLS|SSL.*error"
            r")"
        ),
        "Network failure — retry carefully or report environmental blockage.",
    ),
    (
        "type_error",
        re.compile(
            r"(?i)("
            r"error\s+TS\d+"
            r"|Property\s+'[^']+'\s+does\s+not\s+exist\s+on\s+type"
            r"|is\s+not\s+assignable\s+to\s+type"
            r"|Type\s+error:"
            r"|mypy:"
            r"|error:\s+Argument\s+of\s+type"
            r")"
        ),
        "Type/compiler error — fix typed code or the typed contract.",
    ),
    (
        "assertion",
        re.compile(
            r"(?i)("
            r"AssertionError"
            r"|expect\(.*\)\.(?:to|not)"
            r"|FAILED.*assert"
            r"|Expected:|Received:"
            r"|assert\s+.+\s+==\s+"
            r")"
        ),
        "Assertion failure — investigate behavior vs expectation.",
    ),
    (
        "build_framework",
        re.compile(
            r"(?i)("
            r"Module\s+not\s+found:\s+Can't\s+resolve"
            r"|Failed\s+to\s+compile"
            r"|webpack|Next\.js.*build"
            r"|vite.*build.*failed"
            r")"
        ),
        "Build/framework structure error — fix configuration or imports.",
    ),
    (
        "timeout",
        re.compile(
            r"(?i)("
            r"timed?\s*out"
            r"|TimeoutError"
            r"|exceeded\s+timeout"
            r")"
        ),
        "Timeout — determine deadlock, slowness, or incorrect wait.",
    ),
    (
        "flaky",
        re.compile(
            r"(?i)("
            r"flaky|intermittent|race\s+condition\s+in\s+test"
            r")"
        ),
        "Suspected flake — reproduce and investigate nondeterminism.",
    ),
)


@dataclass(frozen=True)
class FailureClassification:
    """Layered classification of a verification failure."""

    layer: str
    summary: str
    command: str = ""
    exit_code: Optional[int] = None
    # Whether repairing the *verification mechanism* is forbidden by default
    protects_verification: bool = True
    # Suggested causal backtrack target (earliest assumption class)
    backtrack_target: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_BACKTRACK = {
    "command_not_found": "environment_prepared / dependencies_installed",
    "dependency_install": "package_manifest_valid / version_exists_on_registry",
    "permission": "environment_permissions",
    "network": "network_reachable",
    "type_error": "types_match_declared_api",
    "assertion": "behavior_matches_contract",
    "build_framework": "framework_structure_valid",
    "timeout": "test_wait_or_deadlock_assumption",
    "flaky": "determinism_assumption",
    "unknown": "nearest_parent_success_assumption",
}


def classify_failure(
    *,
    output: str = "",
    error: str = "",
    command: str = "",
    exit_code: Optional[int] = None,
    failure_kind: str = "",
) -> FailureClassification:
    """
    Classify a verification failure into an actionable layer.

    ``tsc: command not found`` is *not* a TypeScript error — it is
    ``command_not_found`` / environment preparation.
    """
    blob = "\n".join(p for p in (command, error, output) if p)
    for layer, pattern, summary in _LAYER_PATTERNS:
        if pattern.search(blob):
            return FailureClassification(
                layer=layer,
                summary=summary,
                command=command or "",
                exit_code=exit_code,
                protects_verification=True,
                backtrack_target=_BACKTRACK.get(layer, _BACKTRACK["unknown"]),
            )

    # Fall back on declared failure_kind from the verify pipeline
    kind = (failure_kind or "").strip().lower()
    if kind in ("typecheck", "type_member"):
        return FailureClassification(
            layer="type_error",
            summary=_LAYER_PATTERNS[4][2],  # type_error summary
            command=command or "",
            exit_code=exit_code,
            backtrack_target=_BACKTRACK["type_error"],
        )
    if kind in ("build", "lint"):
        return FailureClassification(
            layer="build_framework",
            summary=_LAYER_PATTERNS[6][2],
            command=command or "",
            exit_code=exit_code,
            backtrack_target=_BACKTRACK["build_framework"],
        )
    if kind in ("test", "relevant_tests"):
        return FailureClassification(
            layer="assertion",
            summary=_LAYER_PATTERNS[5][2],
            command=command or "",
            exit_code=exit_code,
            backtrack_target=_BACKTRACK["assertion"],
        )
    if exit_code not in (None, 0):
        return FailureClassification(
            layer="unknown",
            summary=(
                "Unclassified non-zero exit — preserve the exact failure and "
                "locate the earliest assumption that predicted success."
            ),
            command=command or "",
            exit_code=exit_code,
            backtrack_target=_BACKTRACK["unknown"],
        )
    return FailureClassification(
        layer="unknown",
        summary="No failure signal classified.",
        command=command or "",
        exit_code=exit_code,
        protects_verification=False,
        backtrack_target=_BACKTRACK["unknown"],
    )


def format_classification_for_reflect(cls: FailureClassification) -> str:
    """Append to reflect messages so the model backtracks causally."""
    return (
        f"Failure layer: {cls.layer}\n"
        f"Correct response: {cls.summary}\n"
        f"Causal backtrack target: {cls.backtrack_target}\n"
        "Do NOT weaken the verification mechanism that detected this failure. "
        "Repair the earliest unsupported assumption, then re-run the original check."
    )
