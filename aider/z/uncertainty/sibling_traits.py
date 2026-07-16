"""Sibling shared-trait detection for new files in a pattern family.

When a new file matches an existing family (same-dir peers, etc.), check whether
those siblings share a common *companion* trait — e.g. every peer is listed in a
registry dict, ``__all__``, plugin index, or settings map — and whether the new
file's diff updates that same companion.

This is deliberately project-agnostic: we discover the companion by comparing
what siblings already do on disk, not by hardcoding framework names.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


_SKIP_DIR_PARTS = frozenset(
    {
        "node_modules",
        ".git",
        "venv",
        ".venv",
        "__pycache__",
        ".tox",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
    }
)

_CODE_SUFFIXES = frozenset({".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

# Filenames / dir names that often hold registries / indexes
_REGISTRYISH_NAME_RE = re.compile(
    r"(?i)(settings|default_settings|registry|registries|plugins?|index|"
    r"exports?|commands?|routes?|urls?|middleware|manifest|entry_?points?|"
    r"__init__)$"
)

_REGISTRYISH_BODY_RE = re.compile(
    r"__all__\s*=|"
    r"\b\w*_?BASE\s*=\s*\{|"
    r"\b(MIDDLEWARES?|REGISTRY|REGISTRIES|PLUGINS?|ROUTES?|COMMANDS?|"
    r"ENTRY_?POINTS?|PROVIDERS?|HANDLERS?)\b\s*=|"
    r"\b(register|add_middleware|include_router)\s*\(",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CompanionGap:
    """One mechanically detected missing sibling-companion update."""

    new_file: str
    companion_file: str
    sibling_files: Tuple[str, ...]
    sibling_ids_found: Tuple[str, ...]
    missing_ids: Tuple[str, ...]
    trait_kind: str  # registry | dunder_all | init_exports | cooccurrence
    evidence: str


@dataclass
class _SiblingIds:
    rel: str
    stem: str
    dotted: str
    camel: str
    tokens: Tuple[str, ...] = field(default_factory=tuple)


def identifiers_for(rel: str) -> _SiblingIds:
    path = Path(rel.replace("\\", "/"))
    stem = path.stem
    dotted = rel.replace("\\", "/").replace("/", ".")
    if path.suffix and dotted.endswith(path.suffix):
        dotted = dotted[: -len(path.suffix)]
    parts = re.split(r"[_\-]+", stem)
    camel = "".join(p[:1].upper() + p[1:] for p in parts if p)
    tokens = tuple(
        dict.fromkeys(
            t
            for t in (stem, dotted, camel, f"{dotted}.{camel}" if camel else "")
            if t and len(t) > 1
        )
    )
    return _SiblingIds(rel=rel, stem=stem, dotted=dotted, camel=camel, tokens=tokens)


def new_files_from_diff(diff: str) -> List[str]:
    """Paths introduced as brand-new files in a unified diff."""
    if not diff:
        return []
    out: List[str] = []
    pending_new = False
    for line in diff.splitlines():
        if line.startswith("new file mode"):
            pending_new = True
            continue
        if line.startswith("--- ") and "/dev/null" in line:
            pending_new = True
            continue
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                pending_new = False
                continue
            if raw.startswith("b/"):
                raw = raw[2:]
            if pending_new and raw:
                out.append(raw.replace("\\", "/"))
            pending_new = False
    # de-dupe preserve order
    seen = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _is_skipped(path: Path) -> bool:
    return any(part in _SKIP_DIR_PARTS for part in path.parts)


def _family_siblings(new_file: str, matches: Sequence[str]) -> List[str]:
    """Prefer same-directory peers; fall back to pattern matches."""
    new_parent = Path(new_file.replace("\\", "/")).parent.as_posix()
    same_dir = [
        m.replace("\\", "/")
        for m in matches
        if Path(m.replace("\\", "/")).parent.as_posix() == new_parent
    ]
    if len(same_dir) >= 2:
        return same_dir[:12]
    # Also accept same parent *name* (engine's weak pattern) when enough exist
    new_parent_name = Path(new_file).parent.name
    same_name = [
        m.replace("\\", "/")
        for m in matches
        if Path(m.replace("\\", "/")).parent.name == new_parent_name
    ]
    if len(same_name) >= 2:
        return same_name[:12]
    return [m.replace("\\", "/") for m in matches[:8]]


def _candidate_companion_files(root: Path, family_dir: Path) -> List[Path]:
    """Bounded walk near the family for likely registry/index companions."""
    root = root.resolve()
    try:
        family_dir = family_dir.resolve()
    except OSError:
        family_dir = root

    found: List[Path] = []
    seen: Set[str] = set()

    def _add(p: Path) -> None:
        if not p.is_file() or p.suffix.lower() not in _CODE_SUFFIXES:
            return
        if _is_skipped(p):
            return
        key = str(p)
        if key in seen:
            return
        seen.add(key)
        found.append(p)

    # Family dir + ancestors (settings often live a level or two up)
    cur = family_dir
    for _ in range(5):
        if not cur.exists():
            break
        try:
            for child in cur.iterdir():
                if child.is_file():
                    _add(child)
                elif child.is_dir() and not _is_skipped(child):
                    name = child.name.lower()
                    if _REGISTRYISH_NAME_RE.search(name) or name in {
                        "settings",
                        "conf",
                        "config",
                        "registry",
                        "plugins",
                        "commands",
                    }:
                        for q in child.rglob("*"):
                            if q.is_file():
                                _add(q)
                            if len(found) >= 80:
                                return found
        except OSError:
            pass
        if cur == root or root not in cur.parents and cur != root:
            break
        cur = cur.parent

    return found


def _token_in_text(token: str, text: str) -> bool:
    if not token or not text:
        return False
    if "." in token or "/" in token:
        return token in text
    # bare stem / CamelCase — word-ish boundary
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text) is not None


def _classify_trait(companion_text: str, companion_rel: str) -> str:
    name = Path(companion_rel).name
    if name == "__init__.py":
        return "init_exports"
    if re.search(r"__all__\s*=", companion_text or ""):
        return "dunder_all"
    if _REGISTRYISH_BODY_RE.search(companion_text or "") or _REGISTRYISH_NAME_RE.search(
        Path(companion_rel).stem
    ):
        return "registry"
    return "cooccurrence"


def _diff_adds_ids_for_file(diff: str, companion_rel: str, ids: Sequence[str]) -> bool:
    """True when unified diff adds any of *ids* into *companion_rel*."""
    if not diff or not companion_rel:
        return False
    companion_norm = companion_rel.replace("\\", "/")
    in_file = False
    for line in diff.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw.startswith("b/"):
                raw = raw[2:]
            in_file = raw.replace("\\", "/") == companion_norm
            continue
        if not in_file:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            body = line[1:]
            if any(_token_in_text(i, body) for i in ids):
                return True
    return False


def find_sibling_companion_gaps(
    root: Path,
    *,
    new_file: str,
    sibling_matches: Sequence[str],
    diff: str = "",
    files_changed: Sequence[str] = (),
    file_contents: Optional[Dict[str, str]] = None,
) -> List[CompanionGap]:
    """
    If siblings share a companion registration trait and the new file is missing
    from that companion (and the diff doesn't add it), return gap(s).
    """
    root = Path(root)
    new_file = new_file.replace("\\", "/")
    family = _family_siblings(new_file, sibling_matches)
    # Need enough peers to establish a shared trait (not a one-off)
    if len(family) < 2:
        return []

    sib_ids = [identifiers_for(s) for s in family]
    new_ids = identifiers_for(new_file)
    family_dir = root / Path(new_file).parent

    candidates = _candidate_companion_files(root, family_dir)
    # Never treat the new file or the siblings themselves as the companion
    exclude = {new_file, *family}
    contents_cache = dict(file_contents or {})

    min_hits = max(2, math.ceil(len(sib_ids) * 0.67))
    gaps: List[CompanionGap] = []

    for cand in candidates:
        try:
            crel = cand.relative_to(root).as_posix()
        except ValueError:
            continue
        if crel in exclude:
            continue
        # Skip pure test companions — registration usually lives in production
        crel_l = crel.lower()
        if any(
            p in crel_l
            for p in ("/tests/", "/test/", "test_", "_test.", "/testing/")
        ):
            continue

        text = contents_cache.get(crel)
        if text is None:
            try:
                text = cand.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Cap huge files
            if len(text) > 400_000:
                text = text[:400_000]
            contents_cache[crel] = text

        hit_sibs: List[str] = []
        hit_tokens: List[str] = []
        for sid in sib_ids:
            matched_tok = next((t for t in sid.tokens if _token_in_text(t, text)), None)
            if matched_tok:
                hit_sibs.append(sid.rel)
                hit_tokens.append(matched_tok)

        if len(hit_sibs) < min_hits:
            continue

        trait = _classify_trait(text, crel)
        # Require a registry-ish signal OR family __init__ — plain co-occurrence
        # in a random util file is too noisy.
        if trait == "cooccurrence":
            continue

        # Does the companion already mention the new file?
        if any(_token_in_text(t, text) for t in new_ids.tokens):
            continue

        # Did this turn's diff / changed files add the registration?
        changed = {f.replace("\\", "/") for f in files_changed or []}
        if crel in changed or _diff_adds_ids_for_file(diff, crel, new_ids.tokens):
            # Re-check post-edit content if available
            post = contents_cache.get(crel) or text
            if any(_token_in_text(t, post) for t in new_ids.tokens):
                continue
            if _diff_adds_ids_for_file(diff, crel, new_ids.tokens):
                continue

        missing = tuple(new_ids.tokens[:4])
        gaps.append(
            CompanionGap(
                new_file=new_file,
                companion_file=crel,
                sibling_files=tuple(hit_sibs),
                sibling_ids_found=tuple(dict.fromkeys(hit_tokens))[:12],
                missing_ids=missing,
                trait_kind=trait,
                evidence=(
                    f"{len(hit_sibs)}/{len(family)} siblings appear in {crel} "
                    f"({trait}); new file identifiers {', '.join(missing[:3])} do not."
                ),
            )
        )

    # Prefer registry over init; one gap per companion file
    gaps.sort(
        key=lambda g: (
            0 if g.trait_kind == "registry" else 1 if g.trait_kind == "dunder_all" else 2,
            g.companion_file,
        )
    )
    return gaps[:3]


def summarize_gaps(gaps: Iterable[CompanionGap]) -> str:
    parts = [f"{g.companion_file} ({g.trait_kind})" for g in gaps]
    return ", ".join(parts)
