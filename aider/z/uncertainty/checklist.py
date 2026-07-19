"""Requirement checklist — decompose, bind evidence, semantic gap rescore."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence

from .schema import RequirementItem, TaskChecklist

_STOP = {
    "with",
    "that",
    "this",
    "from",
    "have",
    "should",
    "would",
    "could",
    "into",
    "using",
    "please",
    "also",
    "just",
    "make",
    "sure",
    "your",
    "their",
}


@dataclass
class ItemEvidence:
    item_id: str
    item_text: str
    keyword_hits: List[str] = field(default_factory=list)
    file_hits: List[str] = field(default_factory=list)
    symbol_hits: List[str] = field(default_factory=list)
    test_hits: List[str] = field(default_factory=list)
    log_hits: List[str] = field(default_factory=list)
    decision_hits: List[str] = field(default_factory=list)
    verification_ok: Optional[bool] = None
    kind: str = "product"
    missing: str = ""
    evidence_notes: List[str] = field(default_factory=list)

    def evidence_strings(self) -> List[str]:
        out = []
        out.extend(f"file:{f}" for f in self.file_hits[:5])
        out.extend(f"symbol:{s}" for s in self.symbol_hits[:5])
        out.extend(f"test:{t}" for t in self.test_hits[:5])
        out.extend(f"log:{k}" for k in self.log_hits[:5])
        out.extend(f"decision:{k}" for k in self.decision_hits[:5])
        out.extend(self.evidence_notes[:5])
        if self.verification_ok is not None:
            out.append(f"verify:{'ok' if self.verification_ok else 'fail'}")
        out.extend(f"kw:{k}" for k in self.keyword_hits[:5])
        return out

    @property
    def has_code_evidence(self) -> bool:
        return bool(self.file_hits or self.symbol_hits)

    @property
    def has_test_only_evidence(self) -> bool:
        return bool(self.test_hits) and not self.has_code_evidence

    @property
    def has_process_evidence(self) -> bool:
        return bool(self.log_hits or self.decision_hits) or self.verification_ok is True

    @property
    def has_doc_evidence(self) -> bool:
        return bool(self.file_hits) or any(
            n.startswith("doc:") for n in self.evidence_notes
        )

    @property
    def has_hard_product_evidence(self) -> bool:
        """
        Codex coding-quality bar: Fully Addressed for product/quality requires
        a real file + symbol + test — not keyword vibes alone.
        """
        return bool(self.file_hits) and bool(self.symbol_hits) and bool(self.test_hits)

    def missing_hard_evidence_parts(self) -> List[str]:
        missing = []
        if not self.file_hits:
            missing.append("file")
        if not self.symbol_hits:
            missing.append("symbol")
        if not self.test_hits:
            missing.append("test")
        return missing


# Tooling / agent process (never search product source for these)
_PROCESS_RE = re.compile(
    r"(?i)\b(use|enable|run|with|via)\b.{0,40}\b("
    r"uncertainty|checklist|verify(?:-before-commit)?|commit\s+gate|skills?|auto-act"
    r")\b"
    r"|\b(ask|confirm|decide|review)\b.{0,40}\b(user|me|before)\b"
    r"|\b("
    r"do\s+not\s+commit|don't\s+commit|never\s+commit|"
    r"commit\s+only\s+after|before\s+(finishing|committing)|"
    r"fix\s+failures?\s+before|until\s+(the\s+)?(verified|verification|tests?\s+pass)|"
    r"working\s+tree\s+passes|verified\s+working\s+tree|"
    r"before\s+verification\s+passes"
    r")\b"
)
_VERIFY_RE = re.compile(
    r"(?i)\b(test|tests|verify|verification|smoke\s*test|pytest|unittest|"
    r"run\s+the\s+(complete\s+)?(test\s+)?suite|run\s+the\s+tests?)\b"
)
_DECISION_RE = re.compile(
    r"(?i)\b(confirm|decide|approve|acknowledge|ask\s+(the\s+)?user|"
    r"which\s+\w+|choose\s+between)\b"
)
_DOC_RE = re.compile(
    r"(?i)\b(document|documentation|readme|changelog|api\s+docs?|"
    r"docs?(?:/|\s)|semantics|write\s+up|markdown\s+anchor)\b"
)
_QUALITY_RE = re.compile(
    r"(?i)\b(thread[- ]?safe|concurrency|concurrent|race\s+condition|"
    r"under\s+contention|lock\s+ordering|stress\s+test|performance|"
    r"latency|throughput|security\s+constraint|idempotent)\b"
)
_EXTERNAL_RE = re.compile(
    r"(?i)\b(production\s+api|external\s+(api|service)|assumes?\s+that|"
    r"supports\s+this\s+field|live\s+api|upstream\s+supports)\b"
)
_PRODUCT_VERB_RE = re.compile(
    r"(?i)\b(implement|add|create|build|write|fix|refactor)\b"
)

# Named investigative instructions — trackable obligations, not loose context
_INVESTIGATE_RE = re.compile(
    r"(?i)\b("
    r"(?:also\s+)?(?:please\s+)?"
    r"(?:check|investigate|look\s+(?:at|into)|examine|inspect|rule\s+out|"
    r"consider|watch\s+for|pay\s+attention\s+to|probe)|"
    r"named\s+(?:suspect|area)|specific\s+suspect|"
    r"also\s+(?:check|investigate|look)|"
    r"reallocation\s+race|vector[- ]reallocation"
    r")\b"
)

_INVESTIGATE_CLAUSE_RE = re.compile(
    r"(?is)"
    r"(?:^|[.;\n]|(?<=\s)also\s+)"
    r"(?:(?:also|please)\s+)?"
    r"(?:"
    r"(?:check|investigate|look\s+(?:at|into)|examine|inspect|rule\s+out|"
    r"consider|watch\s+for|pay\s+attention\s+to|probe)\b"
    r"|a\s+named,?\s+specific\s+suspect\b"
    r"|the\s+(?:vector[- ]reallocation|reallocation)\s+race\b"
    r")"
    r"[^.;\n]{6,240}"
)

# Identifiers / paths worth tracking as investigation targets
_TARGET_RE = re.compile(
    r"`([^`]+)`"
    r"|([A-Za-z_][A-Za-z0-9_]{2,}(?:::[A-Za-z_][A-Za-z0-9_]*)*)"
    r"|([\w./+-]+\.(?:c|cc|cpp|cxx|h|hpp|hh|rs|go|py|java|ts|js))"
)

_TARGET_STOP = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "also",
    "check",
    "checks",
    "checking",
    "investigate",
    "investigating",
    "look",
    "into",
    "examine",
    "inspect",
    "rule",
    "out",
    "consider",
    "watch",
    "attention",
    "named",
    "specific",
    "suspect",
    "area",
    "race",
    "condition",
    "vector",
    "reallocation",
    "concurrent",
    "concurrently",
    "could",
    "while",
    "via",
    "unguarded",
    "indexing",
    "grow",
    "grows",
    "please",
    "whether",
    "there",
    "still",
    "possible",
    "issue",
    "bug",
    "fix",
    "segfault",
    "crash",
    "reallocate",
    "concern",
    "explicitly",
    "header",  # ubiquitous field name; not a site by itself
}

# camelCase/paths are strong; bare English / common fields are not.
_WEAK_INVESTIGATION_TARGETS = frozenset(
    {
        "header",
        "size",
        "data",
        "info",
        "index",
        "count",
        "value",
        "buffer",
        "ptr",
        "tmp",
        "state",
        "type",
        "name",
        "file",
        "path",
        "line",
        "code",
        "error",
        "result",
        "thread",
        "mutex",
        "lock",
        "atomic",
        "shared",
        "reader",
        "writer",
        "poll",
        "read",
        "write",
        "load",
        "store",
    }
)

_DUAL_SITE_SPLIT_RE = re.compile(
    r"(?is)\bwhile\b|\bconcurrent(?:ly)?\b|\bracing\b|\brace\s+with\b|\bvs\.?\b"
)


def _is_strong_investigation_target(tok: str) -> bool:
    """Compound/specific symbols are strong; bare common words are not."""
    if not tok:
        return False
    leaf = tok.split("::")[-1].split("/")[-1].strip("`")
    if not leaf:
        return False
    low = leaf.lower()
    if low in _TARGET_STOP or low in _WEAK_INVESTIGATION_TARGETS:
        return False
    if any(
        low.endswith(ext)
        for ext in (
            ".c",
            ".cc",
            ".cpp",
            ".cxx",
            ".h",
            ".hpp",
            ".hh",
            ".rs",
            ".go",
            ".py",
            ".java",
            ".ts",
            ".js",
        )
    ):
        return True
    if "::" in tok or "/" in tok:
        return True
    # camelCase / PascalCase (bgLogInfos, registerLogInfo, logId)
    if re.search(r"[a-z]", leaf) and re.search(r"[A-Z]", leaf):
        return True
    if "_" in leaf and len(leaf) >= 4:
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_]{3,}", leaf):
        return True
    return False


def extract_investigation_targets(text: str) -> List[str]:
    """Named symbols/paths/areas an investigative instruction points at.

    Prefers strong (compound/specific) targets when any exist so bare words
    like ``header`` / ``concurrent`` cannot independently satisfy the item.
    """
    raw: List[str] = []
    for m in _TARGET_RE.finditer(text or ""):
        tok = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if not tok:
            continue
        leaf = tok.split("::")[-1].split("/")[-1]
        if leaf.lower() in _TARGET_STOP or tok.lower() in _TARGET_STOP:
            continue
        if len(leaf) < 3 and "." not in tok:
            continue
        if tok not in raw:
            raw.append(tok)
    strong = [t for t in raw if _is_strong_investigation_target(t)]
    if strong:
        return strong[:12]
    # No compound symbols — keep longer leftovers, still drop known-weak
    weak_filtered = [
        t
        for t in raw
        if t.split("::")[-1].split("/")[-1].lower() not in _WEAK_INVESTIGATION_TARGETS
    ]
    return (weak_filtered or raw)[:12]


def investigation_site_groups(text: str, targets: Optional[Sequence[str]] = None) -> List[List[str]]:
    """Partition targets into sites that each need evidence.

    Hints shaped ``X while concurrent Y`` / ``X while Y`` are a race between
    two named sites — both sides must be touched or inspected before
    ``checked_fixed`` / ``checked_ruled_out``.
    """
    toks = list(targets) if targets is not None else extract_investigation_targets(text)
    if not toks:
        return []
    strong = [t for t in toks if _is_strong_investigation_target(t)] or list(toks)
    m = _DUAL_SITE_SPLIT_RE.search(text or "")
    if not m:
        return [strong]
    left_blob = (text or "")[: m.start()].lower()
    right_blob = (text or "")[m.end() :].lower()
    left = [t for t in strong if _target_in_text(t, left_blob)]
    right = [t for t in strong if _target_in_text(t, right_blob)]
    # Prefer true dual-site when both sides name something
    if left and right:
        return [left, right]
    return [strong]


def extract_investigation_clauses(text: str) -> List[str]:
    """Pull investigative clauses out of a larger bug-fix request."""
    clauses: List[str] = []
    for m in _INVESTIGATE_CLAUSE_RE.finditer(text or ""):
        chunk = re.sub(r"^\s*(?:also|please)\s+", "", m.group(0).strip(), flags=re.I)
        chunk = chunk.strip(" \n\t-.;")
        if len(chunk) < 12:
            continue
        # Must name a concrete target or we can't track it
        if not extract_investigation_targets(chunk) and not re.search(
            r"(?i)\b(race|realloc|overflow|leak|deadlock)\b", chunk
        ):
            continue
        if chunk not in clauses:
            clauses.append(chunk[:400])
    return clauses


def classify_requirement_kind(text: str) -> str:
    """
    product | process | verification | decision | documentation | quality |
    external_assumption | investigation

    Process/decision/docs never require product-source keyword hits.
    Investigation = named area that must be checked (fixed or ruled out).
    """
    t = text or ""
    has_product_verb = bool(_PRODUCT_VERB_RE.search(t))

    # Strong process/finish rules win even when wording includes "fix"
    # (e.g. "Fix failures before finishing").
    if re.search(
        r"(?i)\b("
        r"do\s+not\s+commit|don't\s+commit|never\s+commit|"
        r"fix\s+failures?\s+before|before\s+(finishing|committing)|"
        r"until\s+(the\s+)?(verified|verification|tests?\s+pass)|"
        r"verified\s+working\s+tree|working\s+tree\s+passes"
        r")\b",
        t,
    ):
        return "process"

    # Mixed "build X and use uncertainty" → product (process is session-side)
    if _PROCESS_RE.search(t) and not has_product_verb:
        return "process"
    if (
        _DECISION_RE.search(t) or re.search(r"(?i)\b(which|choose|prefer|should we)\b", t)
    ) and not has_product_verb:
        return "decision"
    if _DOC_RE.search(t) and not re.search(
        r"(?i)\b(implement|add|create|build)\b.{0,30}\b(feature|endpoint|module|class)\b",
        t,
    ):
        # "Document semantics" / "README must describe allow()" → documentation
        if not has_product_verb or re.search(
            r"(?i)\b(document|documentation|readme|docs?)\b", t
        ):
            return "documentation"

    # Investigative hints before quality — "investigate race in X" is not a
    # soft quality vibe; it is a trackable obligation.
    if _INVESTIGATE_RE.search(t) and (
        extract_investigation_targets(t)
        or re.search(r"(?i)\b(race|realloc|overflow|leak|deadlock|suspect)\b", t)
    ):
        # Pure "implement feature X" with incidental "check" stays product
        if not (
            has_product_verb
            and re.search(
                r"(?i)\b(implement|add|create|build)\b.{0,40}\b(feature|endpoint|module)\b",
                t,
            )
            and not re.search(r"(?i)\b(also|investigate|look\s+at|rule\s+out)\b", t)
        ):
            return "investigation"

    if _QUALITY_RE.search(t) and not re.search(
        r"(?i)\b(implement|add|create|build)\b.{0,20}\b(feature|endpoint|module)\b",
        t,
    ):
        return "quality"
    if _EXTERNAL_RE.search(t) and not has_product_verb:
        return "external_assumption"
    if _VERIFY_RE.search(t) and not re.search(
        r"(?i)\b(implement|add|create|build|write)\b.{0,20}\b(feature|endpoint|module|class)\b",
        t,
    ):
        if re.search(r"(?i)^(add|write|create)\s+tests?\b", t.strip()):
            return "verification"
        if re.search(r"(?i)\b(run|execute|smoke|suite|pass)\b", t) and not has_product_verb:
            return "verification"
        if re.search(r"(?i)\brun\s+the\s+tests?\b", t):
            return "verification"
    return "product"


def decompose_request(title: str, user_message: str) -> TaskChecklist:
    """
    Heuristic decomposition of a user request into discrete sub-requirements.

    Prefer explicit numbered/bulleted lists in the user message; otherwise split
    on conjunctions and sentence boundaries into actionable checklist items.

    Investigative clauses ("also check the vector-reallocation race in X") are
    always lifted into their own ``investigation`` items even when embedded in
    a longer bug-fix paragraph — so they cannot be silently skipped.
    """
    text = (user_message or "").strip()
    items: List[RequirementItem] = []
    seen_texts: set[str] = set()

    def _add(chunk: str, *, force_kind: Optional[str] = None) -> None:
        chunk = (chunk or "").strip()
        if len(chunk) < 8:
            return
        key = chunk.lower()
        if key in seen_texts:
            return
        seen_texts.add(key)
        kind = force_kind or classify_requirement_kind(chunk)
        items.append(RequirementItem(text=chunk, kind=kind))

    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", line)
        if m:
            _add(m.group(1).strip())

    if not items:
        parts = re.split(r"(?:;|\.(?:\s|$)|(?:,\s*and\s+)|\band\bthen\b)", text)
        for part in parts:
            part = part.strip(" \n\t-")
            if len(part) < 8:
                continue
            if part.lower() in {"please", "thanks", "thank you"}:
                continue
            chunk = part[0].upper() + part[1:]
            _add(chunk)

    if not items and text:
        _add(text[:500])

    # Lift investigative hints even when the main sentence was classified product
    for clause in extract_investigation_clauses(text):
        _add(clause[0].upper() + clause[1:] if clause else clause, force_kind="investigation")

    return TaskChecklist(
        task_id=str(uuid.uuid4()),
        title=(title or text[:60] or "Task").strip(),
        items=items,
    )


def format_checklist_for_user(checklist: TaskChecklist) -> str:
    lines = [
        f"Task checklist: {checklist.title}",
        "Please confirm or correct these sub-requirements before implementation:",
    ]
    for i, item in enumerate(checklist.items, start=1):
        lines.append(f"  {i}. [{item.kind}] {item.text}")
    return "\n".join(lines)


def apply_gap_analysis(
    checklist: TaskChecklist,
    *,
    addressed_ids: Optional[Sequence[str]] = None,
    partial_ids: Optional[Sequence[str]] = None,
    statuses: Optional[dict[str, str]] = None,
) -> TaskChecklist:
    addressed = set(addressed_ids or [])
    partial = set(partial_ids or [])
    status_map = dict(statuses or {})

    for item in checklist.items:
        if item.id in status_map:
            item.status = status_map[item.id]
        elif item.id in addressed:
            item.status = "Fully Addressed"
        elif item.id in partial:
            item.status = "Partially Addressed"
        else:
            if item.status not in (
                "Fully Addressed",
                "Partially Addressed",
                "Not Addressed",
            ):
                item.status = "Not Addressed"
    return checklist


def _keywords(text: str) -> List[str]:
    return [
        w
        for w in re.findall(r"[a-z0-9_]{4,}", (text or "").lower())
        if w not in _STOP
    ]


def _looks_doc_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    base = Path(p).name
    return (
        base in {"readme.md", "readme.rst", "readme.txt", "readme"}
        or p.startswith("docs/")
        or "/docs/" in p
        or p.endswith(".md")
        or "changelog" in base
    )


def path_looks_docs_artifact(path: str) -> bool:
    """
    Mechanical docs_touched signal: README* / CHANGELOG* / HISTORY* / docs/**.

    Stricter than _looks_doc_path (which also matches any .md) so editing a
    random markdown note does not satisfy a documentation requirement.
    """
    p = (path or "").replace("\\", "/").lower()
    base = Path(p).name
    if base.startswith("readme"):
        return True
    if "changelog" in base or base.startswith("history") or base == "changes.md":
        return True
    if p == "docs" or p.startswith("docs/") or "/docs/" in p:
        return True
    return False


def files_touch_docs(files_changed: Sequence[str]) -> bool:
    return any(path_looks_docs_artifact(f) for f in (files_changed or []))


def _doc_corpus(
    files_changed: Sequence[str],
    file_contents: dict[str, str],
) -> tuple[str, List[str]]:
    """Build a documentation corpus from changed files + common doc paths in contents."""
    hits: List[str] = []
    parts: List[str] = []
    for f, body in file_contents.items():
        if _looks_doc_path(f) or f in files_changed:
            if _looks_doc_path(f):
                hits.append(f)
                parts.append(body.lower()[:8000])
    for f in files_changed:
        if _looks_doc_path(f) and f not in hits:
            hits.append(f)
            parts.append((file_contents.get(f) or "").lower()[:8000])
    # Always consider README-named keys even if not in files_changed
    for key, body in file_contents.items():
        if Path(key).name.lower().startswith("readme") and key not in hits:
            hits.append(key)
            parts.append(body.lower()[:8000])
    return "\n".join(parts), list(dict.fromkeys(hits))


def _added_diff_blob(diff: str) -> str:
    lines = []
    for line in (diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return "\n".join(lines)


def _changed_diff_blob(diff: str) -> str:
    """Added + removed lines only — never unchanged unified-diff context."""
    lines = []
    for line in (diff or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            lines.append(line[1:])
    return "\n".join(lines)


# Explicit file paths in product checklist items (React Flight-style lists).
_PRODUCT_FILE_PATH_RE = re.compile(
    r"`([^`\n]+?\.[A-Za-z0-9]+)`"
    r"|((?:[\w.+@-]+/)+[\w.+@-]+\.[A-Za-z0-9]+)"
    r"|(\b[\w.+@-]+\.(?:js|jsx|ts|tsx|mjs|cjs|cts|mts|py|go|rs|java|"
    r"cpp|cc|cxx|c|h|hpp|hh|cs|kt|swift|rb|php|vue|svelte)\b)"
)

_FILE_LIST_CHANGE_STOP = frozenset(
    {
        "skip",
        "skips",
        "skipping",
        "inconsistent",
        "subset",
        "worse",
        "doing",
        "update",
        "updates",
        "updating",
        "change",
        "changes",
        "changing",
        "file",
        "files",
        "path",
        "paths",
        "variant",
        "variants",
        "across",
        "every",
        "same",
        "line",
        "ones",
        "them",
        "these",
        "those",
        "must",
        "need",
        "needs",
        "needed",
        "required",
        "require",
        "please",
        "make",
        "sure",
        "both",
        "each",
        "all",
        "any",
        "none",
        "touch",
        "touched",
        "edit",
        "edits",
        "edited",
        "apply",
        "applied",
        "add",
        "adds",
        "adding",
        "type",
        "types",
        "one",
        "line",
    }
)


def extract_product_file_paths(text: str) -> List[str]:
    """File paths/locations a product item enumerates (order-preserving unique)."""
    found: List[str] = []
    for m in _PRODUCT_FILE_PATH_RE.finditer(text or ""):
        tok = (m.group(1) or m.group(2) or m.group(3) or "").strip().strip("`'\"")
        if not tok or tok.startswith("http"):
            continue
        # Drop pure extension-less noise / version-like tokens
        leaf = tok.replace("\\", "/").split("/")[-1]
        if "." not in leaf or leaf.startswith("."):
            continue
        if tok not in found:
            found.append(tok)
    return found


def _diff_changed_by_file(diff: str) -> dict[str, str]:
    """Map repo-relative path → concatenated +/- lines for that file's hunks."""
    buckets: dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            m = re.search(r"diff --git a/(.+?) b/(.+)$", line)
            current = (m.group(2).strip() if m else None)
            if current:
                buckets.setdefault(current, [])
            continue
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            if current and current != "/dev/null":
                buckets.setdefault(current, [])
            continue
        if not current:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            buckets.setdefault(current, []).append(line[1:])
    return {path: "\n".join(parts) for path, parts in buckets.items()}


def _normalize_repo_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip().lstrip("./")


def _path_matches_required(required: str, candidate: str) -> bool:
    r = _normalize_repo_path(required).lower()
    c = _normalize_repo_path(candidate).lower()
    if not r or not c:
        return False
    if r == c:
        return True
    if c.endswith("/" + r) or r.endswith("/" + c):
        return True
    if "/" in r:
        return c.endswith(r) or r.endswith(c)
    return Path(c).name == r


def _match_required_path(
    required: str, files_changed: Sequence[str]
) -> Optional[str]:
    for f in files_changed:
        if _path_matches_required(required, f):
            return f
    return None


def _distinctive_change_tokens(text: str, paths: Sequence[str]) -> List[str]:
    """Tokens describing the required edit, excluding enumerated path names."""
    cleaned = text or ""
    for p in paths:
        cleaned = re.sub(re.escape(p), " ", cleaned, flags=re.I)
        leaf = Path(_normalize_repo_path(p)).name
        if leaf:
            cleaned = re.sub(re.escape(leaf), " ", cleaned, flags=re.I)
    tokens: List[str] = []
    for tok in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", cleaned):
        low = tok.lower()
        if (
            low in _STOP
            or low in _TARGET_STOP
            or low in _WEAK_INVESTIGATION_TARGETS
            or low in _FILE_LIST_CHANGE_STOP
        ):
            continue
        camel = bool(re.search(r"[a-z]", tok) and re.search(r"[A-Z]", tok))
        if not camel and len(tok) < 6:
            continue
        if tok not in tokens:
            tokens.append(tok)
    return tokens[:16]


def _lookup_file_changed_blob(
    matched_path: str, required: str, per_file_diff: dict[str, str]
) -> str:
    for key, blob in per_file_diff.items():
        if _path_matches_required(matched_path, key) or _path_matches_required(
            required, key
        ):
            return blob
    return ""


def _file_has_described_change(
    matched_path: str,
    required: str,
    per_file_diff: dict[str, str],
    change_tokens: Sequence[str],
) -> bool:
    """True when +/- hunks for this file show the described change (not mere touch)."""
    blob = _lookup_file_changed_blob(matched_path, required, per_file_diff)
    if not (blob or "").strip():
        return False
    if not change_tokens:
        # Item was only a file list — require a real edit in this file.
        return True
    blob_l = blob.lower()
    return any(tok.lower() in blob_l for tok in change_tokens)


def _target_in_text(target: str, blob: str) -> bool:
    if not target or not blob:
        return False
    t = target.lower()
    leaf = t.split("::")[-1].split("/")[-1]
    return t in blob or (leaf and leaf in blob)


def _site_evidenced(site: Sequence[str], hits: Sequence[str]) -> bool:
    """True when any target in the site appears in the hit list."""
    if not site:
        return False
    hit_l = {h.lower() for h in hits}
    for t in site:
        tl = t.lower()
        leaf = tl.split("::")[-1].split("/")[-1]
        if tl in hit_l or (leaf and leaf in hit_l):
            return True
        # Also accept if a hit string contains the target leaf
        for h in hit_l:
            if leaf and leaf in h:
                return True
    return False


def bind_evidence(
    checklist: TaskChecklist,
    *,
    files_changed: Sequence[str],
    file_contents: Optional[dict[str, str]] = None,
    symbols: Sequence[str] = (),
    test_files: Sequence[str] = (),
    execution_log: str = "",
    user_decisions: Sequence[str] = (),
    verification: Any = None,
    tests_passed: Optional[bool] = None,
    last_diff: str = "",
) -> List[ItemEvidence]:
    """
    Attach concrete evidence to each checklist item.

    Each kind accepts only relevant evidence:
    - process → execution log / verification / git-session facts
    - verification → VerificationRecord / tests_passed
    - documentation → README/docs sections
    - quality → stress/concurrency tests + implementation symbols
    - decision → user decisions
    - investigation → diff touch (checked_fixed) or inspect/grep log (checked_ruled_out)
    - product → implementation symbols + behavioral tests
    """
    contents = file_contents or {}
    corpus_files = " ".join(files_changed).lower()
    corpus_body = " ".join(contents.values()).lower()[:12000]
    corpus_syms = " ".join(symbols).lower()
    corpus_tests = " ".join(test_files).lower()
    log_blob = (execution_log or "").lower()
    decisions_blob = " ".join(user_decisions or []).lower()
    changed_diff = _changed_diff_blob(last_diff).lower()
    doc_blob, doc_files = _doc_corpus(files_changed, contents)

    verify_ok = None
    if verification is not None:
        verify_ok = bool(getattr(verification, "meaningful_pass", False))
    elif tests_passed is True:
        verify_ok = True
    elif tests_passed is False:
        verify_ok = False

    out: List[ItemEvidence] = []

    for item in checklist.items:
        kind = getattr(item, "kind", None) or classify_requirement_kind(item.text)
        item.kind = kind
        words = _keywords(item.text)
        ev = ItemEvidence(item_id=item.id, item_text=item.text, kind=kind)
        text_l = (item.text or "").lower()

        if kind == "process":
            markers = [
                "uncertainty",
                "checklist",
                "verification",
                "verify",
                "commit gate",
                "skill",
                "/uncertainties",
                "gate",
                "tests_passed",
                "working tree",
            ]
            for m in markers:
                if m in log_blob or m in decisions_blob:
                    ev.log_hits.append(m)
            if "uncertainty" in text_l and (
                "uncertainty" in log_blob
                or "uncertainty tree" in log_blob
                or "verify" in log_blob
            ):
                ev.log_hits.append("session_uncertainty")
            # Commit/finish process rules are proven by successful verification
            if verify_ok and re.search(
                r"(?i)commit|fail|finish|verif|working\s+tree|before\s+finish",
                text_l,
            ):
                ev.verification_ok = True
                ev.log_hits.append("verification_passed")
                ev.evidence_notes.append("session:verification_satisfied_process_rule")
            elif verify_ok and ("uncertainty" in text_l or "checklist" in text_l):
                ev.verification_ok = True
            out.append(ev)
            continue

        if kind == "verification":
            ev.verification_ok = verify_ok
            if verify_ok:
                ev.log_hits.append("verification_passed")
                if verification is not None:
                    discovered = getattr(verification, "tests_discovered", None)
                    if discovered:
                        ev.evidence_notes.append(f"tests_discovered:{discovered}")
            elif verification is not None:
                state = getattr(verification, "state", None)
                ev.log_hits.append(f"verification_{getattr(state, 'value', state)}")
            elif tests_passed is True:
                ev.verification_ok = True
                ev.log_hits.append("tests_passed_signal")
            out.append(ev)
            continue

        if kind == "decision":
            if checklist.confirmed_by_user or decisions_blob:
                ev.decision_hits.append("confirmed")
            for w in words:
                if w in decisions_blob:
                    ev.decision_hits.append(w)
            out.append(ev)
            continue

        if kind == "documentation":
            # Concrete docs_touched bar: only docs edited this turn count.
            # Pre-existing README content must not silently satisfy the requirement.
            touched_docs = [f for f in files_changed if path_looks_docs_artifact(f)]
            touched_blob = "\n".join(
                (contents.get(f) or "").lower()[:8000] for f in touched_docs
            )
            for f in touched_docs:
                ev.file_hits.append(f)
            for w in words:
                if w in touched_blob:
                    ev.keyword_hits.append(w)
            if touched_docs:
                ev.evidence_notes.append("docs_touched:true")
                ev.evidence_notes.append(f"doc:{touched_docs[0]}")
                if (
                    any(w in touched_blob for w in words)
                    or re.search(
                        r"(?i)\b(api|allow|usage|semantics|example|redact|changelog)\b",
                        touched_blob,
                    )
                    or re.search(r"(?i)readme|document|docs?|changelog", text_l)
                ):
                    if re.search(r"(?i)readme|document|docs?|changelog", text_l):
                        ev.keyword_hits.append("readme")
            else:
                ev.evidence_notes.append("docs_touched:false")
                ev.missing = (
                    ev.missing
                    or "No README/CHANGELOG/docs/** file was edited this turn"
                )
            ev.file_hits = list(dict.fromkeys(ev.file_hits))
            ev.keyword_hits = list(dict.fromkeys(ev.keyword_hits))
            out.append(ev)
            continue

        if kind == "quality":
            quality_markers = (
                "thread",
                "concurrent",
                "concurrency",
                "race",
                "lock",
                "contention",
                "stress",
                "prune",
            )
            for t in test_files:
                tl = t.lower()
                try:
                    body = (contents.get(t) or "").lower()
                except Exception:
                    body = ""
                blob = tl + " " + body[:4000]
                if any(m in blob for m in quality_markers) or any(
                    w in blob for w in words
                ):
                    ev.test_hits.append(t)
            for f in files_changed:
                body = (contents.get(f) or "").lower()
                if any(m in body for m in quality_markers) or any(
                    w in body for w in words
                ):
                    ev.file_hits.append(f)
            for s in symbols:
                sl = s.lower()
                if any(m in sl for m in quality_markers) or any(w in sl for w in words):
                    ev.symbol_hits.append(s)
            # Partial credit for related product symbols (allow/prune) when quality asks concurrency
            for s in symbols:
                if s.lower() in corpus_body or s in symbols:
                    if re.search(r"(?i)allow|prune|bucket|lock|mutex", s):
                        if s not in ev.symbol_hits:
                            ev.symbol_hits.append(s)
            ev.file_hits = list(dict.fromkeys(ev.file_hits))
            ev.symbol_hits = list(dict.fromkeys(ev.symbol_hits))
            ev.test_hits = list(dict.fromkeys(ev.test_hits))
            out.append(ev)
            continue

        if kind == "external_assumption":
            if "live" in log_blob or "api verified" in log_blob:
                ev.log_hits.append("live_api")
            out.append(ev)
            continue

        if kind == "investigation":
            targets = extract_investigation_targets(item.text)
            if not targets:
                # Fall back to keyword idents from the clause (still prefer strong)
                fallback = [
                    w
                    for w in words
                    if w not in _TARGET_STOP
                    and w not in _WEAK_INVESTIGATION_TARGETS
                    and len(w) >= 4
                ][:8]
                targets = fallback
            sites = investigation_site_groups(item.text, targets)
            if targets:
                ev.evidence_notes.append("targets:" + ",".join(targets[:6]))
            if len(sites) > 1:
                ev.evidence_notes.append(
                    "sites:"
                    + ";".join(",".join(s[:4]) for s in sites)
                )

            fixed_hits: List[str] = []
            inspect_hits: List[str] = []
            weak_hits: List[str] = []
            for t in targets:
                tl = t.lower()
                leaf = tl.split("::")[-1].split("/")[-1]
                # checked_fixed: only +/- changed lines count — never unchanged
                # unified-diff context (fmtlog3: bgLogInfos on a context line).
                in_changed = _target_in_text(t, changed_diff)
                if in_changed and (
                    _is_strong_investigation_target(t)
                    or not any(_is_strong_investigation_target(x) for x in targets)
                ):
                    fixed_hits.append(t)
                    ev.symbol_hits.append(t)
                    if _target_in_text(t, corpus_files):
                        for f in files_changed:
                            if _target_in_text(t, f.lower()) or _target_in_text(
                                t, (contents.get(f) or "").lower()[:4000]
                            ):
                                ev.file_hits.append(f)
                    continue

                # checked_ruled_out: session recorded inspect/grep of the target
                inspect_pat = (
                    f"inspect: {tl}" in log_blob
                    or f"inspect: read {tl}" in log_blob
                    or f"grep: {tl}" in log_blob
                    or f"grep: {leaf}" in log_blob
                    or (
                        ("inspect:" in log_blob or "grep:" in log_blob)
                        and leaf
                        and leaf in log_blob
                    )
                )
                ruled_out_lang = bool(
                    re.search(
                        rf"(?i)\b({re.escape(leaf)}|{re.escape(tl)})\b.{{0,80}}"
                        r"\b(ruled\s+out|no\s+issue|not\s+a\s+(?:bug|problem)|"
                        r"unrelated|false\s+alarm|does\s+not\s+apply)\b",
                        log_blob,
                    )
                )
                if inspect_pat or ruled_out_lang:
                    inspect_hits.append(t)
                    ev.log_hits.append(t)
                    continue

                # Weak: name only mentioned in chat/log without inspect/diff
                if leaf and leaf in log_blob:
                    weak_hits.append(t)
                    ev.keyword_hits.append(t)

            ev.file_hits = list(dict.fromkeys(ev.file_hits))
            ev.symbol_hits = list(dict.fromkeys(ev.symbol_hits))
            ev.log_hits = list(dict.fromkeys(ev.log_hits))

            # Dual-site races need every site evidenced (fixed and/or inspect).
            combined_hits = list(dict.fromkeys(fixed_hits + inspect_hits))
            sites_ok = (
                all(_site_evidenced(site, combined_hits) for site in sites)
                if sites
                else bool(combined_hits)
            )

            if sites_ok and fixed_hits:
                ev.evidence_notes.append("disposition:checked_fixed")
                ev.evidence_notes.append("fixed:" + ",".join(fixed_hits[:6]))
            elif sites_ok and inspect_hits:
                ev.evidence_notes.append("disposition:checked_ruled_out")
                ev.evidence_notes.append("ruled_out:" + ",".join(inspect_hits[:6]))
            elif fixed_hits or inspect_hits or weak_hits:
                # Partial: touched one side of a dual-site hint, or weak mention
                ev.evidence_notes.append("disposition:partial_inspect")
                if fixed_hits:
                    ev.evidence_notes.append("fixed_partial:" + ",".join(fixed_hits[:6]))
                if inspect_hits:
                    ev.evidence_notes.append(
                        "inspect_partial:" + ",".join(inspect_hits[:6])
                    )
            else:
                ev.evidence_notes.append("disposition:not_checked")
            out.append(ev)
            continue

        # Product requirements — code/test evidence
        required_paths = extract_product_file_paths(item.text)
        if len(required_paths) >= 2:
            # Enumerated file list: each named path must show the *described*
            # change in +/- hunks. Touching a subset (or editing the right
            # files for an unrelated fix) must not Fully Address the item.
            per_file_diff = _diff_changed_by_file(last_diff)
            change_tokens = _distinctive_change_tokens(item.text, required_paths)
            covered: List[str] = []
            missing_paths: List[str] = []
            for req in required_paths:
                matched = _match_required_path(req, files_changed)
                if matched and _file_has_described_change(
                    matched, req, per_file_diff, change_tokens
                ):
                    covered.append(req)
                    ev.file_hits.append(matched)
                else:
                    missing_paths.append(req)
            ev.evidence_notes.append(
                "required_files:" + ",".join(required_paths[:12])
            )
            if change_tokens:
                ev.evidence_notes.append(
                    "change_tokens:" + ",".join(change_tokens[:8])
                )
            if missing_paths:
                ev.evidence_notes.append("files_list_incomplete:true")
                ev.evidence_notes.append(
                    "files_missing:" + ",".join(missing_paths[:12])
                )
                ev.missing = (
                    "Required files not updated as described: "
                    + ", ".join(missing_paths)
                )
            else:
                ev.evidence_notes.append("files_list_complete:true")

        for w in words:
            if w in corpus_files or w in corpus_body:
                ev.keyword_hits.append(w)
            if w in corpus_syms:
                ev.symbol_hits.append(w)
            if w in corpus_tests:
                ev.test_hits.append(w)
        if len(required_paths) < 2:
            for f in files_changed:
                fl = f.lower()
                if any(w in fl for w in words):
                    ev.file_hits.append(f)
                else:
                    body = (contents.get(f) or "").lower()
                    if words and sum(1 for w in words if w in body) >= max(
                        1, len(words) // 3
                    ):
                        if f not in ev.file_hits:
                            ev.file_hits.append(f)
            # Also scan implementation files not only tests
            for f, body in contents.items():
                if _looks_doc_path(f):
                    continue
                bl = body.lower()
                if words and sum(1 for w in words if w in bl) >= max(
                    1, len(words) // 3
                ):
                    if f not in ev.file_hits and f not in test_files:
                        ev.file_hits.append(f)
        for t in test_files:
            tl = t.lower()
            if any(w in tl for w in words):
                ev.test_hits.append(t)
        ev.file_hits = list(dict.fromkeys(ev.file_hits))
        ev.symbol_hits = list(dict.fromkeys(ev.symbol_hits))
        ev.test_hits = list(dict.fromkeys(ev.test_hits))
        ev.keyword_hits = list(dict.fromkeys(ev.keyword_hits))
        out.append(ev)
    return out


def _status_from_evidence(ev: ItemEvidence, words: List[str]) -> str:
    """
    Mechanical status via fail-closed evidence strategies.

    Fully Addressed only when a registered kind's checkable predicate holds.
    Unknown kinds never fall through to product keyword vibes.
    """
    from .evidence_strategy import status_from_strategy

    return status_from_strategy(ev, words)


def rescore_checklist_with_evidence(
    checklist: TaskChecklist,
    evidence: Sequence[ItemEvidence],
) -> TaskChecklist:
    """
    Structured rescore using bound evidence.

    Process/verification/decision/docs/investigation items never require the
    product file+symbol+test triad.
    """
    by_id = {e.item_id: e for e in evidence}
    for item in checklist.items:
        ev = by_id.get(item.id) or ItemEvidence(
            item_id=item.id, item_text=item.text, kind=getattr(item, "kind", "product")
        )
        words = _keywords(item.text)
        status = _status_from_evidence(ev, words)
        kind = ev.kind or getattr(item, "kind", "product")
        if status in ("Not Addressed", "Unverifiable"):
            from .evidence_strategy import missing_message_for

            ev.missing = missing_message_for(ev, words)
        elif status == "Partially Addressed":
            parts = ev.missing_hard_evidence_parts()
            missing_files = []
            for n in ev.evidence_notes or []:
                if str(n).startswith("files_missing:"):
                    missing_files = [
                        p for p in str(n).split(":", 1)[-1].split(",") if p.strip()
                    ]
                    break
            if kind == "investigation":
                ev.missing = (
                    f"Named area was mentioned but not inspected or fixed: "
                    f"{item.text}. Record an inspect/grep of the named symbols "
                    f"or touch them in the diff."
                )
            elif kind in ("product", "quality") and missing_files:
                ev.missing = (
                    "Required files not updated as described: "
                    + ", ".join(missing_files)
                )
            elif kind in ("product", "quality") and parts:
                ev.missing = (
                    f"Only partial evidence for: {item.text}. "
                    f"Missing: {', '.join(parts)}. "
                    f"Present: {', '.join(ev.evidence_strings()[:6]) or '(none)'}"
                )
            elif kind == "quality" and ev.has_code_evidence and not ev.test_hits:
                ev.missing = (
                    f"Implementation exists but no race/stress test covering: {item.text}"
                )
            else:
                ev.missing = ev.missing or (
                    f"Only partial evidence for: {item.text}. "
                    f"Present: {', '.join(ev.evidence_strings()[:6]) or '(none)'}"
                )
        item.status = status
        # Persist for drift evidence-stagnation snapshots across reflections
        item.last_evidence = ev
    return checklist


def infer_gap_statuses_from_summary(
    checklist: TaskChecklist,
    implementation_summary: str,
) -> TaskChecklist:
    """
    Lexical fallback when evidence binding is unavailable.

    Fail closed: summary vibes may mark Partial at most — never Fully Addressed.
    Prefer rescore_checklist_with_evidence in the main pipeline.
    """
    summary = (implementation_summary or "").lower()
    for item in checklist.items:
        words = _keywords(item.text)
        if not words:
            item.status = "Not Addressed"
            continue
        hits = sum(1 for w in words if w in summary)
        ratio = hits / max(len(words), 1)
        if ratio >= 0.25:
            item.status = "Partially Addressed"
        else:
            item.status = "Not Addressed"
    return checklist


def rescore_checklist_with_model(
    checklist: TaskChecklist,
    evidence: Sequence[ItemEvidence],
    *,
    model_complete: Any = None,
) -> TaskChecklist:
    """
    Optional structured model pass. `model_complete` is a callable(prompt)->str.

    Fail closed: mechanical evidence status is the ceiling. The model may annotate
    `missing` / notes but cannot raise a requirement above what checkable signals
    support — and cannot talk down a mechanical Fully Addressed.
    """
    from .evidence_strategy import combine_model_and_mechanical

    # Always start from evidence rules
    rescore_checklist_with_evidence(checklist, evidence)
    if not callable(model_complete) or not checklist.items:
        return checklist

    by_ev_pre = {e.item_id: e for e in evidence}
    payload = {
        "items": [
            {
                "id": item.id,
                "text": item.text,
                "kind": getattr(item, "kind", "product"),
                "evidence": (
                    by_ev_pre[item.id].evidence_strings()
                    if item.id in by_ev_pre
                    else []
                ),
                "current_status": item.status,
            }
            for item in checklist.items
        ]
    }
    prompt = (
        "You are scoring whether a coding agent finished each requirement.\n"
        "Return ONLY JSON: {\"items\":[{\"id\":\"...\",\"status\":\"Fully Addressed|"
        "Partially Addressed|Not Addressed\",\"evidence\":[\"...\"],\"missing\":\"...\"}]}\n"
        "Respect requirement kind: process/verification use session evidence only; "
        "documentation uses README/docs; product needs implementation symbols.\n"
        "If evidence is empty for a product behavior requirement, status must be Not Addressed. "
        "Tests-only evidence for product is at most Partially Addressed.\n"
        "If current_status is Unverifiable, keep Unverifiable — do not invent Fully/Partial.\n"
        "Your status cannot exceed the mechanical current_status ceiling.\n"
        f"INPUT:\n{json.dumps(payload)}"
    )
    try:
        raw = model_complete(prompt)
        if not raw:
            return checklist
        from aider.z.llm_json import extract_json_from_response

        data = extract_json_from_response(raw)
        if not data:
            return checklist
        by_ev = {e.item_id: e for e in evidence}
        for row in data.get("items") or []:
            item = next((i for i in checklist.items if i.id == row.get("id")), None)
            if not item:
                continue
            model_status = row.get("status") or item.status
            if model_status not in (
                "Fully Addressed",
                "Partially Addressed",
                "Not Addressed",
                "Unverifiable",
            ):
                continue
            ev = by_ev.get(item.id)
            mechanical = item.status
            if ev is not None:
                mechanical = _status_from_evidence(ev, _keywords(item.text))
            final, _ceilinged = combine_model_and_mechanical(
                mechanical, model_status, ev=ev
            )
            item.status = final
            if ev is not None:
                model_missing = (row.get("missing") or "").strip()
                if model_missing and final != "Fully Addressed":
                    ev.missing = model_missing
                elif not ev.missing:
                    ev.missing = model_missing
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        pass
    return checklist


def checklist_gap_details(
    checklist: TaskChecklist,
    evidence: Sequence[ItemEvidence],
) -> List[dict]:
    """Details for Requirement Gap nodes / auto-act prompts."""
    by_id = {e.item_id: e for e in evidence}
    out = []
    for item in checklist.items:
        if item.status == "Fully Addressed":
            continue
        ev = by_id.get(item.id)
        out.append(
            {
                "id": item.id,
                "text": item.text,
                "status": item.status,
                "kind": getattr(item, "kind", "product"),
                "evidence": ev.evidence_strings() if ev else [],
                "missing": (ev.missing if ev and ev.missing else f"Complete: {item.text}"),
            }
        )
    return out


def ledger_snapshot(
    checklist: TaskChecklist,
    evidence: Sequence[ItemEvidence],
) -> List[dict]:
    """Requirement-to-evidence ledger rows for debugging / gate explanations."""
    by_id = {e.item_id: e for e in evidence}
    rows = []
    for i, item in enumerate(checklist.items, start=1):
        ev = by_id.get(item.id)
        rows.append(
            {
                "id": f"R{i}",
                "item_id": item.id,
                "text": item.text,
                "kind": getattr(item, "kind", "product"),
                "status": item.status,
                "evidence": ev.evidence_strings() if ev else [],
                "missing": (ev.missing if ev else "") or "",
            }
        )
    return rows
