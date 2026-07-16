"""Local type-member ground truth — cheap check without a full compiler.

Before trusting a plausible property/method on a *repo-defined* type
(e.g. ``ctx.worktree`` on ``Context``), confirm the member appears in that
type's declared body. Complements package typecheck; catches the miss even
when verification-command routing is wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple


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
    }
)

# Heads of type declarations — bodies extracted with brace matching.
_INTERFACE_HEAD_RE = re.compile(
    r"(?m)^\s*(?:export\s+)?interface\s+(?P<name>[A-Za-z_][\w]*)\s*"
    r"(?:<[^;{]*>)?\s*(?:extends\s+[^{]+)?\{"
)
_TYPE_ALIAS_HEAD_RE = re.compile(
    r"(?m)^\s*(?:export\s+)?type\s+(?P<name>[A-Za-z_][\w]*)\s*"
    r"(?:<[^;{]*>)?\s*=\s*\{"
)
_CLASS_HEAD_RE = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_][\w]*)\s*"
    r"(?:<[^;{]*>)?\s*(?:extends\s+[^{]+)?(?:implements\s+[^{]+)?\{"
)

# Members inside a type body: name: or name( or readonly name
_MEMBER_RE = re.compile(
    r"(?m)^\s*(?:readonly\s+|public\s+|private\s+|protected\s+|static\s+|async\s+)*"
    r"(?P<name>[A-Za-z_][\w]*)\s*(?:\?|!)?\s*(?:[:(=]|\()"
)

# Param typed as local type: (ctx: Context) / ctx: Context<Metadata>
_PARAM_TYPE_RE = re.compile(
    r"\b(?P<param>[A-Za-z_][\w]*)\s*:\s*(?P<type>[A-Za-z_][\w]*)\b"
)

# Property / method access: ctx.worktree / Effect.catchAll(
# Skip common globals / namespaces we can't resolve locally cheaply.
_ACCESS_RE = re.compile(
    r"\b(?P<recv>[A-Za-z_][\w]*)\.(?P<member>[A-Za-z_][\w]*)\b"
)

_SKIP_RECEIVERS = frozenset(
    {
        "console",
        "Math",
        "JSON",
        "Object",
        "Array",
        "Promise",
        "Number",
        "String",
        "Boolean",
        "Date",
        "Error",
        "process",
        "window",
        "document",
        "this",
        "super",
        "exports",
        "module",
        "require",
        "Bun",
        "Deno",
    }
)



@dataclass
class TypeDecl:
    name: str
    file: str
    line: int
    members: Set[str] = field(default_factory=set)


@dataclass
class TypeMemberIssue:
    file: str
    line: int
    receiver: str
    receiver_type: str
    member: str
    declaration_file: str
    available_sample: Tuple[str, ...] = ()

    def format(self) -> str:
        sample = ", ".join(self.available_sample[:12]) or "(none parsed)"
        return (
            f"{self.file}:{self.line}: '{self.member}' is not a declared member of "
            f"local type '{self.receiver_type}' "
            f"(from {self.declaration_file}; known: {sample})"
        )


@dataclass
class TypeMemberCheckResult:
    passed: bool
    issues: List[TypeMemberIssue] = field(default_factory=list)
    types_indexed: int = 0
    files_scanned: int = 0


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _extract_members(body: str) -> Set[str]:
    members: Set[str] = set()
    for m in _MEMBER_RE.finditer(body or ""):
        name = m.group("name")
        if name in {
            "constructor",
            "if",
            "for",
            "while",
            "switch",
            "return",
            "get",
            "set",
            "typeof",
            "keyof",
            "infer",
            "extends",
            "implements",
            "export",
            "import",
            "from",
            "as",
            "new",
            "readonly",
            "public",
            "private",
            "protected",
            "static",
            "async",
            "declare",
            "namespace",
            "module",
            "type",
            "interface",
            "class",
            "enum",
            "const",
            "let",
            "var",
            "function",
        }:
            continue
        members.add(name)
    return members


def _brace_body(text: str, open_brace_idx: int) -> Optional[str]:
    """Return text inside ``{...}`` starting at *open_brace_idx* (the ``{``)."""
    if open_brace_idx < 0 or open_brace_idx >= len(text) or text[open_brace_idx] != "{":
        return None
    depth = 0
    i = open_brace_idx
    in_str = None
    escape = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_idx + 1 : i]
        i += 1
    return None


def parse_type_declarations(rel: str, text: str) -> Dict[str, TypeDecl]:
    """Parse interface / object-type-alias / class declarations from one file."""
    out: Dict[str, TypeDecl] = {}
    blob = text or ""
    for regex in (_INTERFACE_HEAD_RE, _TYPE_ALIAS_HEAD_RE, _CLASS_HEAD_RE):
        for m in regex.finditer(blob):
            name = m.group("name")
            # Opening brace is the last char of the match
            open_idx = m.end() - 1
            body = _brace_body(blob, open_idx)
            if body is None:
                continue
            decl = TypeDecl(
                name=name,
                file=rel,
                line=_line_of(blob, m.start()),
                members=_extract_members(body),
            )
            prev = out.get(name)
            if prev and len(prev.members) >= len(decl.members):
                continue
            out[name] = decl
    return out


def _iter_code_files(root: Path, *, limit: int = 400) -> List[Path]:
    root = Path(root)
    found: List[Path] = []
    for pattern in ("*.ts", "*.tsx", "*.mts", "*.cts"):
        for p in root.rglob(pattern):
            if any(part in _SKIP_DIR_PARTS for part in p.parts):
                continue
            if p.name.endswith(".d.ts") and ".tsbuildinfo" in p.name:
                continue
            found.append(p)
            if len(found) >= limit:
                return found
    return found


def index_local_types(
    root: Path,
    *,
    focus_files: Sequence[str] = (),
) -> Dict[str, TypeDecl]:
    """
    Index local type declarations.

    Prefer types near edited files (same package tree); fall back to a bounded
    repo walk for name resolution.
    """
    root = Path(root)
    index: Dict[str, TypeDecl] = {}

    # 1) Edited files + their sibling dirs first
    seeds: List[Path] = []
    for rel in focus_files:
        p = root / rel
        if p.is_file() and p.suffix in {".ts", ".tsx", ".mts", ".cts"}:
            seeds.append(p)
            parent = p.parent
            for sib in parent.glob("*.ts"):
                seeds.append(sib)
            for sib in parent.glob("*.tsx"):
                seeds.append(sib)

    seen = set()
    for p in seeds:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            crel = p.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        for name, decl in parse_type_declarations(crel, text).items():
            # Keep first / richer
            if name not in index or len(decl.members) > len(index[name].members):
                index[name] = decl

    # 2) If still thin, scan more of the tree (bounded)
    if len(index) < 8:
        for p in _iter_code_files(root, limit=200):
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                crel = p.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            for name, decl in parse_type_declarations(crel, text).items():
                if name not in index or len(decl.members) > len(index[name].members):
                    index[name] = decl

    return index


def _param_bindings(text: str) -> Dict[str, str]:
    """Map local variable/param names → type names from simple annotations."""
    bindings: Dict[str, str] = {}
    for m in _PARAM_TYPE_RE.finditer(text or ""):
        bindings[m.group("param")] = m.group("type")
    return bindings


def check_file_against_index(
    rel: str,
    text: str,
    type_index: Dict[str, TypeDecl],
) -> List[TypeMemberIssue]:
    """Find property accesses on locally typed receivers missing from the type."""
    issues: List[TypeMemberIssue] = []
    bindings = _param_bindings(text)
    if not bindings:
        return issues

    for m in _ACCESS_RE.finditer(text or ""):
        recv = m.group("recv")
        member = m.group("member")
        if recv in _SKIP_RECEIVERS:
            continue
        type_name = bindings.get(recv)
        if not type_name:
            continue
        # Only check types we indexed from this repo
        decl = type_index.get(type_name)
        if not decl:
            continue
        # If the local type has no parsed members, skip (parser miss — don't noise)
        if not decl.members:
            continue
        if member in decl.members:
            continue
        # Common false positive: methods inherited / from mapped types — we only
        # flag when the declaration body clearly lists *some* members and this
        # one isn't among them.
        issues.append(
            TypeMemberIssue(
                file=rel,
                line=_line_of(text, m.start()),
                receiver=recv,
                receiver_type=type_name,
                member=member,
                declaration_file=decl.file,
                available_sample=tuple(sorted(decl.members)[:12]),
            )
        )
    return issues


def check_local_type_members(
    root: Path,
    edited: Sequence[str],
    *,
    file_contents: Optional[Dict[str, str]] = None,
) -> TypeMemberCheckResult:
    """
    Mechanical check: usages of ``recv.member`` where ``recv`` is annotated as
    a local type must refer to a declared member of that type.
    """
    root = Path(root)
    ts_edited = [
        e.replace("\\", "/")
        for e in edited
        if e.replace("\\", "/").endswith((".ts", ".tsx", ".mts", ".cts"))
    ]
    if not ts_edited:
        return TypeMemberCheckResult(passed=True)

    index = index_local_types(root, focus_files=ts_edited)
    issues: List[TypeMemberIssue] = []
    scanned = 0
    contents = dict(file_contents or {})

    for rel in ts_edited:
        text = contents.get(rel)
        if text is None:
            path = root / rel
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
        scanned += 1
        issues.extend(check_file_against_index(rel, text, index))

    # Dedup
    seen = set()
    uniq: List[TypeMemberIssue] = []
    for iss in issues:
        key = (iss.file, iss.line, iss.receiver_type, iss.member)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(iss)

    return TypeMemberCheckResult(
        passed=not uniq,
        issues=uniq[:20],
        types_indexed=len(index),
        files_scanned=scanned,
    )
