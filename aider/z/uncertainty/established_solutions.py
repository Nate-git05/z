"""Established-solution taxonomy — prefer stdlib / known algorithms.

Same shape as absorption_taxonomy: adding a category is a data row, not a new
ad-hoc detector. Used by:
  - gated planning (mandatory "name the standard or justify custom" section)
  - post-diff detector (invention without plan evidence / standard import)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple


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


def scan_invention_in_diff(
    diff_text: str,
    *,
    categories: Optional[Sequence[EstablishedSolutionCategory]] = None,
) -> List[EstablishedSolutionHit]:
    """Find custom inventions of established-solution categories in a diff.

    Suppresses a category when the same diff also imports/uses the standard.
    """
    cats = list(categories or ESTABLISHED_SOLUTIONS)
    added = _added_lines(diff_text)
    blob = "\n".join(added)
    if not blob.strip():
        return []

    hits: List[EstablishedSolutionHit] = []
    for cat in cats:
        if cat.standard_import_regex.search(blob):
            continue
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
