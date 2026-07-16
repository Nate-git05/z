"""Named taxonomy of failure-absorption code shapes.

Adding a new absorption pattern is a data addition to ABSORPTION_TAXONOMY,
not a new ad-hoc detector function. ``scan_failure_absorption`` is the single
scanner that checks a diff against the whole taxonomy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class AbsorptionPattern:
    """One named failure-absorption shape."""

    pattern_id: str
    title: str
    description: str
    # Applied to each added (+) line, or to a multi-line window when multiline=True.
    regex: re.Pattern
    severity: str = "Medium"
    multiline: bool = False
    # When True, only match lines that look like new/changed code (caller filters).
    added_lines_only: bool = True
    # Trusted patterns may hard-block; new taxonomy rows start informational.
    hard_block: bool = False


# Extensible unit: append entries here; do not add one-off detector functions.
ABSORPTION_TAXONOMY: Tuple[AbsorptionPattern, ...] = (
    AbsorptionPattern(
        pattern_id="getattr_new_param_default",
        title="Permissive getattr Shortcut",
        description=(
            "New optional attribute accessed via getattr(..., False/None) — "
            "absorbs AttributeError instead of wiring the flag through."
        ),
        regex=re.compile(
            r"getattr\s*\(\s*\w+\s*,\s*['\"]([A-Za-z_][\w]*)['\"]\s*,\s*"
            r"(?:False|None|0|\"\"|''|\{\}|\[\])\s*\)"
        ),
        severity="High",
        hard_block=True,  # trusted (logveil); promote peers only after live trust
    ),
    AbsorptionPattern(
        pattern_id="bare_except_pass",
        title="Bare except pass",
        description="Bare except with pass absorbs any failure without surfacing it.",
        regex=re.compile(
            r"except\s*:\s*(?:pass\b|#|$)|except\s+Exception\s*:\s*(?:pass\b|#|$)",
            re.MULTILINE,
        ),
        severity="Medium",
        multiline=True,
        hard_block=False,
    ),
    AbsorptionPattern(
        pattern_id="except_pass_block",
        title="Silent exception swallow",
        description=(
            "try/except that catches a broad exception and only pass/continue/"
            "return None — failure is absorbed instead of fixed or reported."
        ),
        regex=re.compile(
            r"except\s+(?:\w+\s*,\s*)*(?:\w+Error|Exception|BaseException)"
            r"(?:\s+as\s+\w+)?\s*:\s*(?:pass\b|continue\b|return\s+None\b)",
            re.MULTILINE,
        ),
        severity="Medium",
        multiline=True,
        hard_block=False,
    ),
    AbsorptionPattern(
        pattern_id="dict_get_masking_default",
        title="Dict get default mask",
        description=(
            "dict.get(key, default) with a concrete default can mask a missing "
            "required key the same way getattr(..., False) masks a missing attr."
        ),
        regex=re.compile(
            r"\.get\s*\(\s*['\"][A-Za-z_][\w]*['\"]\s*,\s*"
            r"(?:False|None|0|\"\"|''|\{\}|\[\]|True)\s*\)"
        ),
        severity="Low",
        hard_block=False,
    ),
    AbsorptionPattern(
        pattern_id="or_falsey_default",
        title="Falsey or-default absorption",
        description=(
            "Using `x or <literal>` as a silent fallback absorbs None/empty "
            "failures that should often be explicit."
        ),
        regex=re.compile(
            r"\b\w+\s+or\s+(?:False|None|0|\"\"|''|\{\}|\[\]|True)\b"
        ),
        severity="Low",
        hard_block=False,
    ),
)


@dataclass
class AbsorptionHit:
    pattern_id: str
    title: str
    description: str
    severity: str
    evidence: str
    line: str


def _added_python_lines(diff_text: str) -> List[str]:
    lines: List[str] = []
    for line in (diff_text or "").splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        if body.strip().startswith("#"):
            continue
        lines.append(body)
    return lines


def _added_block_text(diff_text: str) -> str:
    return "\n".join(_added_python_lines(diff_text))


def scan_failure_absorption(
    diff_text: str,
    *,
    taxonomy: Optional[Sequence[AbsorptionPattern]] = None,
    pattern_ids: Optional[Iterable[str]] = None,
) -> List[AbsorptionHit]:
    """Scan *diff_text* against the failure-absorption taxonomy.

    Returns one hit per (pattern, matching line/snippet). Dedupes by
    (pattern_id, evidence) so the same line is not reported twice for one pattern.
    """
    patterns = list(taxonomy or ABSORPTION_TAXONOMY)
    if pattern_ids is not None:
        allow = set(pattern_ids)
        patterns = [p for p in patterns if p.pattern_id in allow]

    added_lines = _added_python_lines(diff_text)
    block = _added_block_text(diff_text)
    hits: List[AbsorptionHit] = []
    seen = set()

    for pattern in patterns:
        if pattern.multiline:
            for match in pattern.regex.finditer(block):
                snippet = match.group(0).strip()
                key = (pattern.pattern_id, snippet)
                if key in seen or not snippet:
                    continue
                seen.add(key)
                hits.append(
                    AbsorptionHit(
                        pattern_id=pattern.pattern_id,
                        title=pattern.title,
                        description=pattern.description,
                        severity=pattern.severity,
                        evidence=snippet[:200],
                        line=snippet.splitlines()[0][:160],
                    )
                )
            continue

        for line in added_lines:
            if not pattern.regex.search(line):
                continue
            key = (pattern.pattern_id, line.strip())
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                AbsorptionHit(
                    pattern_id=pattern.pattern_id,
                    title=pattern.title,
                    description=pattern.description,
                    severity=pattern.severity,
                    evidence=line.strip()[:200],
                    line=line.strip()[:160],
                )
            )

    return hits


def taxonomy_pattern_ids() -> Tuple[str, ...]:
    return tuple(p.pattern_id for p in ABSORPTION_TAXONOMY)
