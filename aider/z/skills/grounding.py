"""Ground skill capture in real repo evidence (diff + files + symbols).

Prevents invented APIs (e.g. TokenBucket when the code has SlidingWindowRateLimiter)
by packing evidence at capture time and checking named symbols before save.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

# Backtick / heading / keyword identifiers in skill markdown.
# Backticks may contain dotted paths (`Foo.bar`) — each segment is claimed.
_CLAIMED_BACKTICK_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")
_CLAIMED_HEADING_RE = re.compile(
    r"(?m)^\s*#{1,4}\s+([A-Za-z_][A-Za-z0-9_]{2,})\b"
)
_CLAIMED_KEYWORD_RE = re.compile(
    r"\b(?:class|def|function|func|type|interface|struct)\s+"
    r"([A-Za-z_][A-Za-z0-9_]{2,})\b"
)

# Common English / markdown words that look like identifiers
_SYMBOL_STOP = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "when",
    "then",
    "else",
    "true",
    "false",
    "null",
    "none",
    "self",
    "cls",
    "args",
    "kwargs",
    "return",
    "import",
    "export",
    "const",
    "let",
    "var",
    "type",
    "class",
    "function",
    "async",
    "await",
    "steps",
    "step",
    "example",
    "examples",
    "notes",
    "pitfalls",
    "conventions",
    "overview",
    "usage",
    "setup",
    "todo",
    "http",
    "https",
    "json",
    "yaml",
    "markdown",
    "python",
    "typescript",
    "javascript",
}

# Languages where we extract via regex (non-Python)
_RE_CLASS = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:abstract\s+)?(?:class|interface|struct|type|enum)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)
_RE_FUNC = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:function|func|def|fn)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
    r"|^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\("
)
# Explicit Rust/Python method forms (fn/def) — never ambiguous with if/while.
_RE_METHOD_EXPLICIT = re.compile(
    r"(?m)^\s+(?:async\s+)?(?:pub(?:\([^)]*\))?\s+)?(?:fn|def)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)
# C-family / Java / etc.: ReturnType name(params) { — requires a type token
# before the name so `if (x == 0) {` cannot match.
_RE_METHOD_TYPED = re.compile(
    r"(?m)^\s+"
    r"(?:(?:public|private|protected|static|virtual|inline|constexpr|explicit|"
    r"friend|async|override|export|mutable|volatile|typename)\s+)*"
    r"(?:[\w:]+(?:\s*<[^;{}()>]*>)?(?:\s*[*&])?\s+)+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\(([^;{}]*)\)\s*"
    r"(?:const\s*)?(?:noexcept(?:\s*\([^)]*\))?\s*)?(?:override\s*)?(?:final\s*)?"
    r"[{:]"
)
# Untyped JS/TS-style methods: name(params) { / name(params):
# Paren body must look like a parameter list, not a control-flow expression.
_RE_METHOD_LOOSE = re.compile(
    r"(?m)^\s+(?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*[:{]"
)
# Call sites in added diff lines: entries_.pop_back() / handle.resolve(
_RE_METHOD_CALL = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Tokens that introduce `name ( ... ) {` as *statements* in C-family / JS /
# Go / Rust / etc. Closed grammatical set — not an English stopword list and
# not grown reactively one live failure at a time.
_STATEMENT_INTRODUCERS = frozenset(
    {
        "if",
        "else",
        "elif",
        "while",
        "for",
        "do",
        "switch",
        "case",
        "catch",
        "try",
        "finally",
        "except",
        "return",
        "throw",
        "new",
        "delete",
        "sizeof",
        "typeof",
        "alignof",
        "noexcept",
        "match",
        "select",
        "defer",
        "go",
        "range",
        "when",
        "unless",
        "with",
        "assert",
        "yield",
        "await",  # top-level await (...); not a method decl
        "instanceof",
    }
)

# Operators / forms that appear in conditions, not in ordinary parameter lists.
_CONTROL_EXPR_RE = re.compile(
    r"(==|!=|<=|>=|&&|\|\||\b(?:not|and|or)\b|"
    r"(?<![\w:])!(?=[\w(])|"
    r"(?<![\w\s:])<(?![=<\w/])|(?<![\w\s:])>(?![=>\w]))"
)

# Per-file and total budgets for the grounding pack (chars)
DEFAULT_FILE_BUDGET = 8000
DEFAULT_DIFF_BUDGET = 12000
DEFAULT_PACK_BUDGET = 28000


@dataclass
class FileEvidence:
    path: str
    content: str
    symbols: List[str] = field(default_factory=list)
    truncated: bool = False


@dataclass
class GroundingPack:
    """Evidence bundle passed into skill generation and the grounding check."""

    user_request: str = ""
    files: List[FileEvidence] = field(default_factory=list)
    diff: str = ""
    symbols: List[str] = field(default_factory=list)
    root: str = ""

    @property
    def source_files(self) -> List[str]:
        return [f.path for f in self.files]

    def content_hash(self) -> str:
        h = hashlib.sha256()
        h.update((self.user_request or "").encode("utf-8", errors="replace"))
        h.update(b"\0")
        h.update((self.diff or "").encode("utf-8", errors="replace"))
        for fe in self.files:
            h.update(fe.path.encode("utf-8", errors="replace"))
            h.update(b"\0")
            h.update(fe.content.encode("utf-8", errors="replace"))
            h.update(b"\0")
        return h.hexdigest()[:16]


@dataclass
class GroundingResult:
    ok: bool
    grounded_symbols: List[str] = field(default_factory=list)
    missing_symbols: List[str] = field(default_factory=list)
    invented_ratio: float = 0.0
    reason: str = ""


def extract_symbols_from_source(path: str, content: str) -> List[str]:
    """Extract class/function/method names from source text."""
    if not content or not content.strip():
        return []
    suf = Path(path or "").suffix.lower()
    if suf == ".py":
        return _extract_python_symbols(content)
    return _extract_regex_symbols(content)


def _extract_python_symbols(content: str) -> List[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return _extract_regex_symbols(content)

    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name and not node.name.startswith("_"):
                names.append(node.name)
            elif node.name and node.name.startswith("_") and not node.name.startswith("__"):
                # Keep single-underscore helpers that are still part of the API surface
                names.append(node.name)
    # Prefer public names first, de-dupe preserving order
    return _dedupe(names)


def _paren_looks_like_control_expression(inside: str) -> bool:
    """True for `if (x == 0)` conditions; False for typical parameter lists."""
    s = (inside or "").strip()
    if not s:
        return False
    return bool(_CONTROL_EXPR_RE.search(s))


def _loose_method_name_ok(name: str, paren_inside: str) -> bool:
    """Accept untyped `name(...) {{` only when it cannot be a control statement."""
    if not name or name.lower() in _SYMBOL_STOP:
        return False
    # Grammar: statement introducers are never method declarations.
    if name.lower() in _STATEMENT_INTRODUCERS:
        return False
    if _paren_looks_like_control_expression(paren_inside):
        return False
    return True


def _extract_regex_symbols(content: str) -> List[str]:
    names: List[str] = []
    for rx in (_RE_CLASS, _RE_FUNC, _RE_METHOD_EXPLICIT):
        for m in rx.finditer(content):
            for g in m.groups():
                if g and g.lower() not in _SYMBOL_STOP:
                    names.append(g)
    for m in _RE_METHOD_TYPED.finditer(content):
        name = m.group(1) or ""
        if name and name.lower() not in _SYMBOL_STOP and name.lower() not in _STATEMENT_INTRODUCERS:
            names.append(name)
    for m in _RE_METHOD_LOOSE.finditer(content):
        name = m.group(1) or ""
        paren = m.group(2) or ""
        if _loose_method_name_ok(name, paren):
            names.append(name)
    return _dedupe(names)


def _dedupe(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def extract_claimed_symbols(text: str) -> List[str]:
    """Pull identifier-like names the skill claims exist in the codebase."""
    if not text:
        return []
    found: List[str] = []

    def _add(name: str) -> None:
        if not name or len(name) < 3:
            return
        if name.lower() in _SYMBOL_STOP:
            return
        if name.isupper() and len(name) <= 3:
            return  # HTTP, URL, etc.
        found.append(name)

    for m in _CLAIMED_BACKTICK_RE.finditer(text):
        for part in (m.group(1) or "").split("."):
            _add(part)
    for m in _CLAIMED_HEADING_RE.finditer(text):
        _add(m.group(1) or "")
    for m in _CLAIMED_KEYWORD_RE.finditer(text):
        _add(m.group(1) or "")
    return _dedupe(found)


def extract_call_site_names(text: str) -> List[str]:
    """Method/call-site names from source or added-diff text (``.foo(``)."""
    names: List[str] = []
    for m in _RE_METHOD_CALL.finditer(text or ""):
        name = m.group(1) or ""
        if not name or len(name) < 3:
            continue
        if name.lower() in _SYMBOL_STOP:
            continue
        names.append(name)
    return _dedupe(names)


def build_grounding_pack(
    *,
    user_request: str,
    files_changed: Sequence[str],
    root: Optional[Path] = None,
    diff: str = "",
    file_budget: int = DEFAULT_FILE_BUDGET,
    diff_budget: int = DEFAULT_DIFF_BUDGET,
    pack_budget: int = DEFAULT_PACK_BUDGET,
) -> GroundingPack:
    """
    Build evidence from the files just edited.

    `files_changed` may be absolute or repo-relative. Contents are read from disk
    (final state). Diff is optional but strongly preferred.
    """
    root_path = Path(root or Path.cwd()).resolve()
    pack = GroundingPack(
        user_request=(user_request or "").strip(),
        diff=_truncate(diff or "", diff_budget),
        root=str(root_path),
    )

    remaining = pack_budget - len(pack.diff) - len(pack.user_request)
    all_symbols: List[str] = []

    for raw in list(files_changed or [])[:30]:
        if remaining <= 500:
            break
        abs_path, rel = _resolve_path(raw, root_path)
        if not abs_path or not abs_path.is_file():
            continue
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Skip huge binaries / generated dumps
        if len(text) > 400_000 or "\0" in text[:2048]:
            continue

        budget = min(file_budget, remaining)
        truncated = len(text) > budget
        body = text if not truncated else text[:budget] + "\n… [truncated]\n"
        symbols = extract_symbols_from_source(rel, text)
        pack.files.append(
            FileEvidence(path=rel, content=body, symbols=symbols, truncated=truncated)
        )
        all_symbols.extend(symbols)
        remaining -= len(body) + len(rel) + 32

    pack.symbols = _dedupe(all_symbols)
    return pack


def format_grounding_pack(pack: GroundingPack) -> str:
    """Serialize the pack for the model prompt — only this evidence may be cited."""
    parts: List[str] = []
    parts.append("## User request")
    parts.append(pack.user_request or "(none)")
    parts.append("")
    parts.append("## Symbols present in changed files")
    if pack.symbols:
        parts.append(", ".join(pack.symbols[:80]))
    else:
        parts.append("(none extracted)")
    parts.append("")
    if pack.diff.strip():
        parts.append("## Git diff (recent changes)")
        parts.append("```diff")
        parts.append(pack.diff.rstrip())
        parts.append("```")
        parts.append("")
    parts.append("## Final file contents")
    for fe in pack.files:
        parts.append(f"### {fe.path}")
        if fe.symbols:
            parts.append(f"Symbols: {', '.join(fe.symbols[:40])}")
        fence = _fence_lang(fe.path)
        parts.append(f"```{fence}")
        parts.append(fe.content.rstrip())
        parts.append("```")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def check_bug_pattern_grounding(skill, pack: GroundingPack) -> GroundingResult:
    """
    Ground a bug_pattern skill: claimed root_cause_category must match real
    diff evidence (same fail-closed spirit as established_solutions).
    """
    from .bug_concepts import category_grounded_in_diff, concept_by_id

    category = (getattr(skill, "root_cause_category", None) or "").strip()
    if not category:
        return GroundingResult(
            ok=False,
            grounded_symbols=[],
            missing_symbols=[],
            invented_ratio=1.0,
            reason="bug_pattern missing root_cause_category",
        )
    if concept_by_id(category) is None:
        return GroundingResult(
            ok=False,
            grounded_symbols=[],
            missing_symbols=[category],
            invented_ratio=1.0,
            reason=f"unknown root_cause_category (not in curated taxonomy): {category}",
        )
    ok, reason = category_grounded_in_diff(category, pack.diff or "")
    if not ok:
        # Plausible category, evidence_regex miss — record blind-spot signal.
        # Not for unknown/missing category (those return earlier above).
        if (reason or "").startswith("diff lacks evidence"):
            try:
                from .bug_concepts import _added_blob
                from .taxonomy_candidates import record_grounding_miss

                record_grounding_miss(
                    category,
                    _added_blob(pack.diff or ""),
                    getattr(skill, "id", "") or "",
                    skill_title=getattr(skill, "title", "") or "",
                )
            except Exception:
                pass
            try:
                skill.grounding_miss_reason = reason
            except Exception:
                pass
        return GroundingResult(
            ok=False,
            grounded_symbols=[],
            missing_symbols=[category],
            invented_ratio=1.0,
            reason=reason,
        )
    # Evidence matched — clear any prior miss stamp from a failed retry.
    if getattr(skill, "grounding_miss_reason", None):
        try:
            skill.grounding_miss_reason = None
        except Exception:
            pass
    # Also run ordinary symbol grounding on content (soft — category is the hard gate).
    # grounded_symbols must stay code identifiers from the diff/AST — never append
    # root_cause_category taxonomy labels (those live on skill.root_cause_category).
    text = (
        f"{getattr(skill, 'title', '')}\n"
        f"{getattr(skill, 'root_cause_explanation', '')}\n"
        f"{getattr(skill, 'content', '')}"
    )
    sym = check_grounding(text, pack)
    grounded = [
        s
        for s in (sym.grounded_symbols or [])
        if (s or "").strip() and (s or "").strip().lower() != category.lower()
    ]
    return GroundingResult(
        ok=True,
        grounded_symbols=grounded,
        missing_symbols=list(sym.missing_symbols),
        invented_ratio=sym.invented_ratio,
        reason=f"bug_pattern grounded ({reason})",
    )


def check_grounding(
    skill_text: str,
    pack: GroundingPack,
    *,
    max_invented_ratio: float = 0.25,
    min_claimed: int = 1,
) -> GroundingResult:
    """
    Verify that classes/methods named in the skill exist in the grounding pack.

    Returns ok=False when the skill invents symbols not present in evidence.
    Skills that name no symbols at all still pass (convention-only playbooks),
    but capture flows should prefer symbol-backed skills.
    """
    claimed = extract_claimed_symbols(skill_text)
    evidence: Set[str] = set(pack.symbols)
    # Also allow matches against raw file text (methods nested oddly, etc.)
    blob = "\n".join(fe.content for fe in pack.files)
    for name in list(claimed):
        if name in evidence:
            continue
        # Accept if the exact identifier appears as a definition-ish token in evidence
        if re.search(rf"\b{re.escape(name)}\b", blob):
            evidence.add(name)

    if not claimed:
        return GroundingResult(
            ok=True,
            grounded_symbols=[],
            missing_symbols=[],
            invented_ratio=0.0,
            reason="no concrete symbols claimed",
        )

    missing = [c for c in claimed if c not in evidence]
    grounded = [c for c in claimed if c in evidence]
    ratio = len(missing) / max(len(claimed), 1)

    # Allow a little noise (generic words that slipped through) but fail hard
    # when most named APIs are invented.
    if len(missing) >= min_claimed and ratio > max_invented_ratio:
        return GroundingResult(
            ok=False,
            grounded_symbols=grounded,
            missing_symbols=missing,
            invented_ratio=ratio,
            reason=(
                f"skill names {len(missing)} symbol(s) not in changed files: "
                + ", ".join(missing[:8])
            ),
        )

    # Soft fail: any PascalCase invented type is a strong smell
    invented_types = [m for m in missing if m[:1].isupper() and "_" not in m]
    if invented_types:
        return GroundingResult(
            ok=False,
            grounded_symbols=grounded,
            missing_symbols=missing,
            invented_ratio=ratio,
            reason=(
                "skill invents type/class name(s) not in code: "
                + ", ".join(invented_types[:8])
            ),
        )

    return GroundingResult(
        ok=True,
        grounded_symbols=grounded,
        missing_symbols=missing,
        invented_ratio=ratio,
        reason="grounded",
    )


def symbols_still_present(
    symbols: Sequence[str],
    *,
    root: Optional[Path] = None,
    source_files: Sequence[str] = (),
) -> Tuple[List[str], List[str]]:
    """
    Retrieval-time stale check: which grounded symbols still exist on disk?

    Returns (present, missing).
    """
    root_path = Path(root or Path.cwd()).resolve()
    present: List[str] = []
    missing: List[str] = []
    texts: List[str] = []
    for raw in source_files or []:
        abs_path, _rel = _resolve_path(raw, root_path)
        if abs_path and abs_path.is_file():
            try:
                texts.append(abs_path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    blob = "\n".join(texts)
    for sym in symbols or []:
        if not sym:
            continue
        if blob and re.search(rf"\b{re.escape(sym)}\b", blob):
            present.append(sym)
        else:
            missing.append(sym)
    return present, missing


def make_ungrounded_skill_node(
    *,
    skill_title: str,
    missing_symbols: Sequence[str],
    source_files: Sequence[str],
    reason: str,
):
    """Build an uncertainty node when a captured skill may invent APIs."""
    from aider.z.uncertainty.schema import (
        Area,
        NodeStatus,
        NodeType,
        Tier,
        UncertaintyNode,
    )

    missing = list(missing_symbols or [])[:12]
    files = list(source_files or [])[:12]
    title = f"Skill may invent APIs: {skill_title[:60]}"
    return UncertaintyNode(
        title=title,
        type=NodeType.API_ASSUMPTION,
        confidence_tier=Tier.LOW,
        risk_tier=Tier.MEDIUM,
        summary=(
            "Captured skill names symbols that were not found in the changed files."
        ),
        explanation=reason or "Grounding check failed.",
        files_affected=files,
        symbols_affected=missing,
        why_uncertain=(
            "The skill was generated from a task summary and may describe a different "
            "API than the one in the repo (e.g. TokenBucket vs SlidingWindowRateLimiter)."
        ),
        what_could_go_wrong=(
            "Future sessions may follow invented steps and write the wrong code."
        ),
        suggested_fix=(
            "Review the skill body against the real implementation; edit or delete it "
            "before accepting (needs_review)."
        ),
        suggested_tests=[],
        suggested_prompt=(
            f"Rewrite skill '{skill_title}' using only the real symbols in "
            f"{', '.join(files[:3]) or 'the changed files'}: "
            + (", ".join(missing) if missing else "verify named APIs exist")
        ),
        status=NodeStatus.NEEDS_HUMAN_REVIEW,
        area=Area.OTHER,
        signals={
            "skill_grounding": True,
            "missing_symbols": missing,
            "skill_title": skill_title,
        },
    )


def _resolve_path(raw: str, root: Path) -> Tuple[Optional[Path], str]:
    p = Path(raw)
    if p.is_absolute():
        try:
            rel = str(p.resolve().relative_to(root))
        except ValueError:
            rel = p.name
        return p if p.is_file() else None, rel
    abs_path = (root / p).resolve()
    return (abs_path if abs_path.is_file() else None), str(p).replace("\\", "/")


def _truncate(text: str, budget: int) -> str:
    if not text or len(text) <= budget:
        return text or ""
    return text[:budget] + "\n… [diff truncated]\n"


def _fence_lang(path: str) -> str:
    return {
        ".py": "python",
        ".go": "go",
        ".rs": "rust",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".java": "java",
        ".rb": "ruby",
        ".md": "markdown",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
    }.get(Path(path).suffix.lower(), "")
