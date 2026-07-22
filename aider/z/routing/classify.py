"""Pre-edit task classification → CapabilityTier.

Reuses uncertainty.risk.collect_base_signals and schema.text_looks_high_stakes
rather than re-implementing path heuristics.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Sequence, Tuple

from .registry import CapabilityTier

_SKIP_DIR_PARTS = frozenset(
    {
        "node_modules",
        ".git",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        ".tox",
        "vendor",
        "target",
    }
)

_CODE_SUFFIXES = frozenset(
    {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".cpp", ".cc", ".h", ".hpp"}
)

_HARD_TOPIC_RE = re.compile(
    r"(?i)\b(race|concurren|deadlock|migrat|security|auth)\b"
)

# Closed-list domain tag, used as a soft selection preference (select.py) —
# never a hard filter. Order matters: checked top-to-bottom, first match
# wins. concurrency/api are checked before ui/web to reduce overlap on
# generic frontend words; this is a tiebreak judgment call, not a guarantee
# of correctness.
DOMAIN_TAGS: Tuple[str, ...] = ("concurrency", "api", "ui", "web", "math")

_DOMAIN_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (
        "concurrency",
        re.compile(
            r"(?i)\b(race|concurren|deadlock|mutex|thread[- ]?safe|atomic|"
            r"async|await|\block(?:ing)?\b)\b"
        ),
    ),
    (
        "api",
        re.compile(
            r"(?i)\b(rest\s?api|graphql|openapi|swagger|endpoint|webhook|"
            r"grpc|http\s+(?:get|post|put|delete|patch))\b"
        ),
    ),
    (
        "ui",
        re.compile(
            r"(?i)\b(ui\b|component|button|layout|responsive|accessibility|"
            r"a11y|css|frontend|render(?:ing)?)\b"
        ),
    ),
    (
        "web",
        re.compile(
            r"(?i)\b(browser|dom\b|fetch\(|cors|websocket|url\s+routing|"
            r"http\s+server)\b"
        ),
    ),
    (
        "math",
        re.compile(
            r"(?i)\b(algorithm|complexity|big-o|matrix|equation|numerical|"
            r"floating[- ]point|statistic)\b"
        ),
    ),
)

# Domain -> specialty_tags on ModelProfile that are a *justified* correlate
# today. Only "math" has real correlate data in MODEL_REGISTRY (the existing
# "reasoning" tag on o3-mini/claude-sonnet-5/claude-opus-4-8). Deliberately no
# entries for concurrency/api/ui/web — no registry row is honestly tagged for
# them yet; fabricating one would be fake differentiation, not routing logic.
DOMAIN_TAG_ALIASES: dict = {
    "math": ("reasoning",),
}


def domain_from_text(text: Optional[str]) -> Optional[str]:
    """Closed-list domain tag from cheap keyword heuristics.

    Same "Stage 1 is heuristics, not ML" posture as classify_task() — a soft
    selection preference (see select.py), never a hard filter.
    """
    blob = (text or "").strip()
    if not blob:
        return None
    for domain, pattern in _DOMAIN_PATTERNS:
        if pattern.search(blob):
            return domain
    return None


def estimate_context_tokens(files: Sequence[str], *, root: Optional[Path] = None) -> int:
    """Rough token estimate from on-disk file sizes (≈4 chars/token)."""
    total_chars = 0
    base = Path(root) if root is not None else None
    for rel in files or ():
        path = Path(rel)
        if base is not None and not path.is_absolute():
            path = base / path
        try:
            if path.is_file():
                total_chars += min(path.stat().st_size, 400_000)
        except OSError:
            continue
    return max(1, total_chars // 4)


def estimate_blast_radius(
    root: Path,
    target_files: Sequence[str],
    *,
    limit: int = 400,
) -> int:
    """Bounded count of other code files that mention stems from *target_files*."""
    root = Path(root)
    stems = []
    for rel in target_files or ():
        stem = Path(rel).stem
        if stem and len(stem) > 2 and stem not in ("index", "init", "main", "util", "utils"):
            stems.append(stem)
    if not stems:
        return 0

    hits = 0
    scanned = 0
    try:
        for path in root.rglob("*"):
            if scanned >= limit:
                break
            if not path.is_file():
                continue
            if path.suffix.lower() not in _CODE_SUFFIXES:
                continue
            if any(part in _SKIP_DIR_PARTS for part in path.parts):
                continue
            # Don't count the targets themselves
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if rel in set(f.replace("\\", "/") for f in target_files):
                continue
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if len(text) > 400_000:
                text = text[:400_000]
            if any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(s)}(?![A-Za-z0-9_])", text) for s in stems):
                hits += 1
    except OSError:
        pass
    return hits


def _classify_via_cheap_model_call(request_text: str) -> Optional[CapabilityTier]:
    """Optional ambiguous-case classifier — Stage 1 returns None (→ MODERATE)."""
    del request_text
    return None


def classify_task(
    root: Path,
    request_text: str,
    target_files: Sequence[str],
) -> CapabilityTier:
    """Score task difficulty from existing Z signals + light heuristics."""
    from aider.z.uncertainty.risk import collect_base_signals
    from aider.z.uncertainty.schema import text_looks_high_stakes

    base_signals = collect_base_signals(list(target_files or ()))

    score = 0
    if text_looks_high_stakes(request_text or ""):
        score += 2
    if base_signals.high_stakes_hit or base_signals.migration_hit:
        score += 2
    if estimate_blast_radius(Path(root), target_files) > 5:
        score += 1
    if _HARD_TOPIC_RE.search(request_text or ""):
        score += 2

    if score >= 4:
        return CapabilityTier.REASONING_HEAVY
    if score >= 2:
        return CapabilityTier.HARD
    if score >= 1:
        return CapabilityTier.MODERATE

    return _classify_via_cheap_model_call(request_text or "") or CapabilityTier.MODERATE
