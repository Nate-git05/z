"""Established-solution taxonomy — prefer stdlib / known algorithms.

Same shape as absorption_taxonomy: adding a category is a data row, not a new
ad-hoc detector. Used by:
  - gated planning (mandatory "name the standard or justify custom" section)
  - post-diff detector (invention without plan evidence / standard import)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Categories whose invention signal is a class/function name — the real
# standard often lives in an untouched sibling, not the inventing file.
_REPO_WIDE_STANDARD_CATEGORIES = frozenset(
    {"lru_cache", "priority_queue", "concurrency_primitives"}
)

_SKIP_DIR_PARTS = frozenset(
    {
        "node_modules",
        ".git",
        "dist",
        "build",
        ".next",
        "coverage",
        "__pycache__",
        ".venv",
        "venv",
        "vendor",
        "target",
    }
)

_REPO_SCAN_SUFFIXES = frozenset(
    {".py", ".pyi", ".go", ".js", ".jsx", ".ts", ".tsx", ".java", ".rs"}
)

# Triple-quoted first so Python docstrings are removed as units; then
# ordinary "..." / '...' (incl. raw prefixes used in diffs).
_STRING_LITERAL_RE = re.compile(
    r"(?x)"
    r"(?:"
    r'(?:[rRuUfFbB]{0,2})"""(?:\\.|.)*?(?<!\\)"""'
    r"|(?:[rRuUfFbB]{0,2})'''(?:\\.|.)*?(?<!\\)'''"
    r'|(?:[rRuUfFbB]{0,2})"(?:\\.|[^"\\])*"'
    r"|(?:[rRuUfFbB]{0,2})'(?:\\.|[^'\\])*'"
    r"|`(?:\\.|[^`\\])*`"  # JS/TS template literals
    r")",
    re.DOTALL,
)


@dataclass(frozen=True)
class EstablishedSolutionCategory:
    """One well-known problem with an established correct approach."""

    category_id: str
    title: str
    description: str
    # Human-readable standard approaches (shown in the plan)
    standard_names: Tuple[str, ...]
    # Match request / checklist text → planning should ask about this category
    request_regex: re.Pattern
    # Match added diff lines that look like a from-scratch invention
    invention_regex: re.Pattern
    # Match added diff lines that show the standard approach was used
    standard_import_regex: re.Pattern
    severity: str = "Medium"
    hard_block: bool = False


# Extensible unit: append rows here.
ESTABLISHED_SOLUTIONS: Tuple[EstablishedSolutionCategory, ...] = (
    EstablishedSolutionCategory(
        category_id="ipv4_parsing",
        title="IPv4 address parsing / validation",
        description=(
            "Validating or parsing IPv4 addresses is a solved problem. "
            "Hand-rolled dotted-quad regexes reject legal forms (leading zeros) "
            "and accept illegal ones (overflow octets, extra segments)."
        ),
        standard_names=(
            "Python: ipaddress.ip_address / ip_network / IPv4Address",
            "Go: net.ParseIP / netip.ParseAddr",
            "Rust: std::net::Ipv4Addr",
            "JS/TS: prefer a vetted IP library over a custom regex",
        ),
        request_regex=re.compile(
            r"(?i)\b(ipv4|ip\s*v4|ip[- ]?address|dotted[- ]quad|"
            r"redact(?:ion)?\s+(?:of\s+)?ip|ip\s+redact|"
            r"validate\s+ip|parse\s+ip|ip\s+validat)\b"
        ),
        invention_regex=re.compile(
            r"(?:"
            r"re\.(?:compile|match|search|fullmatch|findall)\s*\([^)]*"
            r"(?:\\d\{1,3\}|\[0-9\]\{1,3\}|\\d\+|\[0-9\]\+)"
            r"[^)]*\\\."
            r"[^)]*(?:\\d\{1,3\}|\[0-9\]\{1,3\}|\\d\+|\[0-9\]\+)"
            r"|"
            r"r?['\"][^'\"]*\\d\{1,3\}[^'\"]*\\\.[^'\"]*\\d\{1,3\}[^'\"]*['\"]"
            r")"
        ),
        standard_import_regex=re.compile(
            r"(?i)\b(?:import\s+ipaddress|from\s+ipaddress\s+import|"
            r"ipaddress\.(?:ip_address|ip_network|IPv4Address|IPv4Network)|"
            r"net\.ParseIP|netip\.ParseAddr|Ipv4Addr::)\b"
        ),
        severity="Medium",
    ),
    EstablishedSolutionCategory(
        category_id="email_parsing",
        title="Email address parsing / validation",
        description="Email validation has established parsers; ad-hoc regex is brittle.",
        standard_names=(
            "Python: email.utils.parseaddr / email-validator",
            "Prefer RFC-aware libraries over a single regex",
        ),
        request_regex=re.compile(
            r"(?i)\b(email\s*(?:address|validat|pars)|validate\s+email|parse\s+email)\b"
        ),
        invention_regex=re.compile(
            r"re\.(?:compile|match|search|fullmatch)\s*\([^)]*@"
            r"[^)]*(?:\\w|\[A-Za-z)|"
            r"r?['\"][^'\"]*@[^'\"]*\\.[^'\"]*['\"]"
        ),
        standard_import_regex=re.compile(
            r"(?i)\b(?:from\s+email(?:\.utils)?\s+import|import\s+email|"
            r"email\.utils\.parseaddr|import\s+email_validator|"
            r"validate_email\s*\()\b"
        ),
    ),
    EstablishedSolutionCategory(
        category_id="url_parsing",
        title="URL parsing",
        description="URL parsing belongs to the platform URL library, not a custom regex.",
        standard_names=(
            "Python: urllib.parse.urlparse / urlsplit",
            "JS: URL / URLSearchParams",
            "Go: net/url.Parse",
        ),
        request_regex=re.compile(
            r"(?i)\b(url\s*(?:pars|validat)|parse\s+url|validate\s+url|"
            r"query\s*string)\b"
        ),
        invention_regex=re.compile(
            r"re\.(?:compile|match|search|fullmatch)\s*\([^)]*"
            r"(?:https?://|www\\\.|\\?[^)]*=)[^)]*\)"
        ),
        standard_import_regex=re.compile(
            r"(?i)\b(?:from\s+urllib\.parse\s+import|import\s+urllib\.parse|"
            r"urlparse\s*\(|urlsplit\s*\(|new\s+URL\s*\(|url\.Parse\s*\()\b"
        ),
    ),
    EstablishedSolutionCategory(
        category_id="datetime_parsing",
        title="Date / time parsing",
        description="Date parsing has stdlib/well-known parsers; custom split/regex drifts.",
        standard_names=(
            "Python: datetime.fromisoformat / strptime; dateutil.parser",
            "JS: Temporal / Date parsing libraries",
            "Go: time.Parse",
        ),
        request_regex=re.compile(
            r"(?i)\b(parse\s+(?:a\s+)?(?:date|time|timestamp)|"
            r"date\s*(?:pars|validat|format)|timestamp\s*pars|"
            r"isoformat|strptime)\b"
        ),
        invention_regex=re.compile(
            r"re\.(?:compile|match|search|fullmatch)\s*\([^)]*"
            r"(?:\\d\{4\}|\\d\{1,2\}[/\\-]\\d\{1,2\})[^)]*\)"
        ),
        standard_import_regex=re.compile(
            r"(?i)\b(?:from\s+datetime\s+import|import\s+datetime|"
            r"datetime\.(?:fromisoformat|strptime)|dateutil|"
            r"time\.Parse\s*\()\b"
        ),
    ),
    EstablishedSolutionCategory(
        category_id="uuid_parsing",
        title="UUID parsing / generation",
        description="UUIDs have a stdlib type; do not validate with a hand-rolled hex regex.",
        standard_names=(
            "Python: uuid.UUID / uuid.uuid4",
            "JS: crypto.randomUUID",
            "Go: google/uuid or github.com/gofrs/uuid",
        ),
        request_regex=re.compile(
            r"(?i)\b(uuid|guid)\b"
        ),
        invention_regex=re.compile(
            r"re\.(?:compile|match|search|fullmatch)\s*\([^)]*"
            r"(?:\[0-9a-fA-F\]\{8\}|\\d|[0-9a-f]\{8})[^)]*-[^)]*\)"
        ),
        standard_import_regex=re.compile(
            r"(?i)\b(?:import\s+uuid|from\s+uuid\s+import|uuid\.(?:UUID|uuid4)|"
            r"crypto\.randomUUID|uuid\.New\s*\()\b"
        ),
    ),
    EstablishedSolutionCategory(
        category_id="priority_queue",
        title="Priority queue / heap",
        description="Use the language heap/priority-queue — do not roll a binary heap by hand.",
        standard_names=(
            "Python: heapq",
            "JS: avoid ad-hoc heap unless justified; prefer a known package",
            "Go: container/heap",
            "Java: PriorityQueue",
        ),
        request_regex=re.compile(
            r"(?i)\b(priority\s*queue|min[- ]?heap|max[- ]?heap|heapq)\b"
        ),
        invention_regex=re.compile(
            r"(?i)\b(?:def\s+_?(?:sift|heapify|bubble_(?:up|down))|"
            r"class\s+\w*Heap\b)"
        ),
        standard_import_regex=re.compile(
            r"(?i)\b(?:import\s+heapq|from\s+heapq\s+import|container/heap|"
            r"PriorityQueue)\b"
        ),
    ),
    EstablishedSolutionCategory(
        category_id="lru_cache",
        title="LRU / bounded cache",
        description="Bounded LRU caches are a solved stdlib/data-structure problem.",
        standard_names=(
            "Python: functools.lru_cache / cachetools",
            "Java: LinkedHashMap access-order",
            "Guava Cache / Caffeine",
        ),
        request_regex=re.compile(
            r"(?i)\b(lru\s*cache|least[- ]recently[- ]used|bounded\s+cache)\b"
        ),
        invention_regex=re.compile(
            r"(?i)\b(?:class\s+\w*LRU\w*|OrderedDict\s*\(\s*\).*move_to_end)"
        ),
        standard_import_regex=re.compile(
            r"(?i)\b(?:from\s+functools\s+import\s+(?:lru_cache|cache)|"
            r"functools\.lru_cache|import\s+cachetools|@lru_cache)\b"
        ),
    ),
    EstablishedSolutionCategory(
        category_id="concurrency_primitives",
        title="Concurrency primitives (locks / queues / pools)",
        description=(
            "Prefer language locks, queues, and executors over ad-hoc busy-wait "
            "flags or hand-rolled worker pools."
        ),
        standard_names=(
            "Python: threading.Lock / queue.Queue / concurrent.futures",
            "asyncio.Lock / asyncio.Queue",
            "Go: sync.Mutex / channels",
        ),
        request_regex=re.compile(
            r"(?i)\b(thread[- ]?safe|mutex|lock|worker\s*pool|thread\s*pool|"
            r"concurrent(?:\.futures)?|race\s*condition|asyncio\.(?:Lock|Queue))\b"
        ),
        invention_regex=re.compile(
            r"(?i)\b(?:while\s+.*(?:busy|spin|waiting).*:"
            r"|time\.sleep\s*\(\s*0\s*\)\s*#\s*spin"
            r"|class\s+\w*SpinLock\b)"
        ),
        standard_import_regex=re.compile(
            r"(?i)\b(?:import\s+threading|from\s+threading\s+import|"
            r"import\s+queue|from\s+queue\s+import|"
            r"concurrent\.futures|asyncio\.(?:Lock|Queue)|sync\.Mutex)\b"
        ),
    ),
)


@dataclass
class EstablishedSolutionHit:
    category_id: str
    title: str
    description: str
    standard_names: Tuple[str, ...]
    severity: str
    hard_block: bool
    evidence: str
    kind: str  # "request" | "invention"


@dataclass
class EstablishedSolutionConsideration:
    """Plan row: name the standard approach, or justify a custom one."""

    category_id: str
    problem_category: str
    standard_approach: str = ""
    # use_standard | custom | not_applicable | unspecified
    decision: str = "unspecified"
    custom_justification: str = ""

    def is_satisfied(self) -> bool:
        d = (self.decision or "").strip().lower()
        if d == "use_standard" and (self.standard_approach or "").strip():
            return True
        if d == "custom" and (self.custom_justification or "").strip():
            return True
        if d == "not_applicable":
            return True
        # Named standard without explicit decision still counts as consideration
        if (self.standard_approach or "").strip() and d in ("", "unspecified"):
            return True
        return False


def category_by_id(category_id: str) -> Optional[EstablishedSolutionCategory]:
    for c in ESTABLISHED_SOLUTIONS:
        if c.category_id == category_id:
            return c
    return None


def match_request_categories(text: str) -> List[EstablishedSolutionCategory]:
    blob = text or ""
    out: List[EstablishedSolutionCategory] = []
    for cat in ESTABLISHED_SOLUTIONS:
        if cat.request_regex.search(blob):
            out.append(cat)
    return out


def _added_lines(diff_text: str) -> List[str]:
    lines: List[str] = []
    for line in (diff_text or "").splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        if body.strip().startswith("#"):
            continue
        lines.append(body)
    return lines


def _strip_string_literals(text: str) -> str:
    """Remove string/docstring/template-literal contents (leave other code)."""
    return _STRING_LITERAL_RE.sub(" ", text or "")


def _code_for_suppression(text: str) -> str:
    """Text that may suppress invention — strings do not count as real usage.

    ``#`` full-line comments are already dropped from diff added-lines; for
    file contents we also drop trailing ``#`` comments after string stripping.
    """
    no_strings = _strip_string_literals(text)
    out: List[str] = []
    for line in no_strings.splitlines():
        if "#" in line:
            line = line.split("#", 1)[0]
        out.append(line)
    return "\n".join(out)


def _standard_used_in_text(
    cat: EstablishedSolutionCategory, text: str
) -> bool:
    if not text or not text.strip():
        return False
    return bool(cat.standard_import_regex.search(_code_for_suppression(text)))


def _iter_repo_code_files(
    root: Path,
    *,
    focus_files: Sequence[str] = (),
    limit: int = 120,
) -> List[Path]:
    """Prefer siblings of edited files, then a bounded repo walk."""
    root = Path(root)
    found: List[Path] = []
    seen: Set[str] = set()

    def _add(p: Path) -> None:
        if len(found) >= limit:
            return
        try:
            key = str(p.resolve())
        except OSError:
            return
        if key in seen or not p.is_file():
            return
        if p.suffix.lower() not in _REPO_SCAN_SUFFIXES:
            return
        if any(part in _SKIP_DIR_PARTS for part in p.parts):
            return
        seen.add(key)
        found.append(p)

    for rel in focus_files or ():
        p = root / str(rel).replace("\\", "/")
        if p.is_file():
            _add(p)
            parent = p.parent
            try:
                for sib in parent.iterdir():
                    _add(sib)
                    if len(found) >= limit:
                        return found
            except OSError:
                pass

    if len(found) >= limit:
        return found
    try:
        for p in root.rglob("*"):
            _add(p)
            if len(found) >= limit:
                break
    except OSError:
        pass
    return found


def _repo_has_standard_usage(
    cat: EstablishedSolutionCategory,
    root: Path,
    *,
    focus_files: Sequence[str] = (),
) -> bool:
    """Bounded disk search for a real standard import/call (not in a string)."""
    for path in _iter_repo_code_files(root, focus_files=focus_files):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(text) > 400_000:
            continue
        if _standard_used_in_text(cat, text):
            return True
    return False


def _standard_used(
    cat: EstablishedSolutionCategory,
    *,
    diff_blob: str,
    file_contents: Optional[Dict[str, str]] = None,
    root: Optional[Path] = None,
    focus_files: Optional[Sequence[str]] = None,
) -> bool:
    """True when real (non-string) code uses the category's standard approach."""
    if _standard_used_in_text(cat, diff_blob):
        return True
    for text in (file_contents or {}).values():
        if _standard_used_in_text(cat, text):
            return True
    if (
        cat.category_id in _REPO_WIDE_STANDARD_CATEGORIES
        and root is not None
    ):
        focus = list(focus_files or (file_contents or {}).keys())
        if _repo_has_standard_usage(cat, Path(root), focus_files=focus):
            return True
    return False


def scan_invention_in_diff(
    diff_text: str,
    *,
    categories: Optional[Sequence[EstablishedSolutionCategory]] = None,
    file_contents: Optional[Dict[str, str]] = None,
    root: Optional[Path] = None,
    focus_files: Optional[Sequence[str]] = None,
) -> List[EstablishedSolutionHit]:
    """Find custom inventions of established-solution categories in a diff.

    Suppresses a category when real code uses the standard — in the diff,
    in touched file contents, or (for class/function-name categories) via a
    bounded repo-wide search. String/docstring mentions do **not** suppress;
    invention matching still sees string contents (e.g. ``re.compile(r"...")``).
    """
    cats = list(categories or ESTABLISHED_SOLUTIONS)
    added = _added_lines(diff_text)
    blob = "\n".join(added)
    if not blob.strip():
        return []

    focus = list(focus_files or (file_contents or {}).keys())
    hits: List[EstablishedSolutionHit] = []
    for cat in cats:
        if _standard_used(
            cat,
            diff_blob=blob,
            file_contents=file_contents,
            root=root,
            focus_files=focus,
        ):
            continue
        # Invention check uses the full blob — string contents matter
        # (hand-rolled re.compile(r"...ipv4...") lives inside quotes).
        m = cat.invention_regex.search(blob)
        if not m:
            continue
        hits.append(
            EstablishedSolutionHit(
                category_id=cat.category_id,
                title=cat.title,
                description=cat.description,
                standard_names=cat.standard_names,
                severity=cat.severity,
                hard_block=cat.hard_block,
                evidence=m.group(0).strip()[:200],
                kind="invention",
            )
        )
    return hits


def considerations_from_text(text: str) -> List[EstablishedSolutionConsideration]:
    """Seed plan rows for categories implied by the request."""
    rows: List[EstablishedSolutionConsideration] = []
    for cat in match_request_categories(text):
        rows.append(
            EstablishedSolutionConsideration(
                category_id=cat.category_id,
                problem_category=cat.title,
                standard_approach=cat.standard_names[0] if cat.standard_names else "",
                decision="unspecified",
                custom_justification="",
            )
        )
    return rows


def plan_covers_category(
    considerations: Sequence[EstablishedSolutionConsideration],
    category_id: str,
) -> bool:
    """True when the plan recorded a satisfied consideration for the category."""
    for row in considerations or []:
        if row.category_id == category_id and row.is_satisfied():
            return True
    return False


def plan_allows_custom_invention(
    considerations: Sequence[EstablishedSolutionConsideration],
    category_id: str,
) -> bool:
    """
    True only when the plan explicitly justified a custom implementation.

    Naming ``use_standard`` does *not* suppress an invention hit — the diff
    must also import/call the standard (handled by scan_invention_in_diff).
    """
    for row in considerations or []:
        if row.category_id != category_id:
            continue
        d = (row.decision or "").strip().lower()
        if d == "custom" and (row.custom_justification or "").strip():
            return True
    return False


def taxonomy_category_ids() -> Tuple[str, ...]:
    return tuple(c.category_id for c in ESTABLISHED_SOLUTIONS)
