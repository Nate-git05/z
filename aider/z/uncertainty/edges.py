"""Structural edge-case detection from control-flow (not model self-report).

Enumerates branches/conditionals in changed files via AST (Python) or regex
fallback, then flags branches that look unhandled and undiscussed — independent
of whatever edge-case list the model chose to admit.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set


@dataclass
class StructuralBranch:
    path: str
    lineno: int
    kind: str
    condition: str
    enclosing: str = ""
    end_lineno: int = 0

    def label(self) -> str:
        cond = (self.condition or "").strip()
        if len(cond) > 72:
            cond = cond[:69] + "…"
        where = f" in {self.enclosing}" if self.enclosing else ""
        return f"{self.kind}{where}: {cond}" if cond else f"{self.kind}{where}"


# Kinds that are especially likely to be "weird data" paths
_EDGE_KINDS = {
    "else",
    "elif",
    "except",
    "match_case",
    "none_check",
    "empty_check",
    "falsy_guard",
    "bound_check",
}

_NONE_RE = re.compile(r"\bis\s+None\b|\b==\s*None\b|\bis\s+not\s+None\b")
_EMPTY_RE = re.compile(
    r"""==\s*(?:''|""|\[\]|\{\}|0)(?:\s|$)|"""
    r"""!=\s*(?:''|""|\[\]|\{\})(?:\s|$)|"""
    r"""\bis\s+(?:''|""|\[\])"""
)
_FALSY_RE = re.compile(r"^\s*not\s+\w+")
_BOUND_RE = re.compile(r"[<>]=?\s*\d+|\blen\s*\(")

# Non-Python fallback: crude branch line detection
_RE_BRANCH = re.compile(
    r"(?m)^\s*(?:else\b|elif\b|else\s*:|catch\s*\(|except\s+|case\s+|default\s*:|"
    r"if\s+.+\s*(?:==\s*null|!=\s*null|==\s*None|is\s+None))"
)


def extract_structural_branches(
    path: str,
    source: str,
    *,
    changed_lines: Optional[Set[int]] = None,
) -> List[StructuralBranch]:
    """Extract edge-relevant branches from one file. Prefer AST for Python."""
    if not source or not source.strip():
        return []
    suf = Path(path).suffix.lower()
    if suf == ".py":
        return _extract_python_branches(path, source, changed_lines=changed_lines)
    return _extract_regex_branches(path, source, changed_lines=changed_lines)


def _extract_python_branches(
    path: str,
    source: str,
    *,
    changed_lines: Optional[Set[int]] = None,
) -> List[StructuralBranch]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _extract_regex_branches(path, source, changed_lines=changed_lines)

    branches: List[StructuralBranch] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: List[str] = []

        def _enclosing(self) -> str:
            return self.stack[-1] if self.stack else ""

        def visit_FunctionDef(self, node: ast.FunctionDef):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_ClassDef(self, node: ast.ClassDef):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_If(self, node: ast.If):
            cond = _unparse(node.test)
            lineno = getattr(node, "lineno", 0) or 0
            end = getattr(node, "end_lineno", lineno) or lineno
            kind = _classify_condition(cond)
            if kind:
                branches.append(
                    StructuralBranch(
                        path=path,
                        lineno=lineno,
                        end_lineno=end,
                        kind=kind,
                        condition=cond,
                        enclosing=self._enclosing(),
                    )
                )
            # else / elif chain
            if node.orelse:
                else_lineno = lineno
                if (
                    len(node.orelse) == 1
                    and isinstance(node.orelse[0], ast.If)
                ):
                    # elif — visit will handle the nested If
                    pass
                else:
                    else_lineno = getattr(node.orelse[0], "lineno", lineno) or lineno
                    else_end = getattr(node.orelse[-1], "end_lineno", else_lineno) or else_lineno
                    branches.append(
                        StructuralBranch(
                            path=path,
                            lineno=else_lineno,
                            end_lineno=else_end,
                            kind="else",
                            condition=f"not ({cond})" if cond else "else",
                            enclosing=self._enclosing(),
                        )
                    )
            self.generic_visit(node)

        def visit_ExceptHandler(self, node: ast.ExceptHandler):
            typ = _unparse(node.type) if node.type else "bare except"
            lineno = getattr(node, "lineno", 0) or 0
            end = getattr(node, "end_lineno", lineno) or lineno
            branches.append(
                StructuralBranch(
                    path=path,
                    lineno=lineno,
                    end_lineno=end,
                    kind="except",
                    condition=typ,
                    enclosing=self._enclosing(),
                )
            )
            self.generic_visit(node)

        def visit_Match(self, node: ast.AST):  # type: ignore[name-defined]
            # Python 3.10+
            for case in getattr(node, "cases", []) or []:
                pattern = _unparse(getattr(case, "pattern", None)) or "case"
                lineno = getattr(case, "lineno", getattr(node, "lineno", 0)) or 0
                end = getattr(case, "end_lineno", lineno) or lineno
                branches.append(
                    StructuralBranch(
                        path=path,
                        lineno=lineno,
                        end_lineno=end,
                        kind="match_case",
                        condition=pattern,
                        enclosing=self._enclosing(),
                    )
                )
            self.generic_visit(node)

    Visitor().visit(tree)

    if changed_lines:
        branches = [
            b
            for b in branches
            if b.lineno in changed_lines
            or (b.end_lineno and any(b.lineno <= ln <= b.end_lineno for ln in changed_lines))
        ]
    # Prefer edge-ish kinds; keep plain `if` only when classified as edge check
    return [b for b in branches if b.kind in _EDGE_KINDS]


def _classify_condition(cond: str) -> str:
    cond = (cond or "").strip()
    if not cond:
        return ""
    if _NONE_RE.search(cond):
        return "none_check"
    if _EMPTY_RE.search(cond):
        return "empty_check"
    if _FALSY_RE.search(cond) or cond.startswith("not "):
        return "falsy_guard"
    if _BOUND_RE.search(cond):
        return "bound_check"
    # Generic if — not automatically an edge; elif/else/except cover the rest
    return ""


def _extract_regex_branches(
    path: str,
    source: str,
    *,
    changed_lines: Optional[Set[int]] = None,
) -> List[StructuralBranch]:
    branches: List[StructuralBranch] = []
    for m in _RE_BRANCH.finditer(source):
        line_start = source.count("\n", 0, m.start()) + 1
        if changed_lines and line_start not in changed_lines:
            continue
        snippet = m.group(0).strip()[:80]
        kind = "else"
        low = snippet.lower()
        if low.startswith("elif") or low.startswith("else if"):
            kind = "elif"
        elif "except" in low or low.startswith("catch"):
            kind = "except"
        elif "case" in low or low.startswith("default"):
            kind = "match_case"
        elif "none" in low or "null" in low:
            kind = "none_check"
        branches.append(
            StructuralBranch(
                path=path,
                lineno=line_start,
                end_lineno=line_start,
                kind=kind,
                condition=snippet,
            )
        )
    return branches


def branch_is_discussed(branch: StructuralBranch, discussed_text: str) -> bool:
    """True if agent reply / model edge list appears to mention this branch."""
    blob = (discussed_text or "").lower()
    if not blob:
        return False
    tokens = _tokens(branch.condition) | _tokens(branch.enclosing) | {branch.kind}
    # Require at least one distinctive token overlap (len>=4) or enclosing name
    for t in tokens:
        if len(t) >= 4 and t in blob:
            return True
    if branch.enclosing and branch.enclosing.lower() in blob and any(
        k in blob for k in ("edge", "else", "empty", "null", "none", "error", "fail")
    ):
        return True
    return False


def branch_has_test_signal(
    branch: StructuralBranch,
    *,
    test_blob: str,
) -> bool:
    """Weak signal: a relevant test mentions the enclosing function/class."""
    if not test_blob or not branch.enclosing:
        return False
    return branch.enclosing in test_blob


def select_undiscussed_branches(
    branches: Sequence[StructuralBranch],
    *,
    discussed_text: str = "",
    test_blob: str = "",
    limit: int = 4,
) -> List[StructuralBranch]:
    """
    Keep branches that look like real edge paths and were not discussed / tested.

    Caps noise — quality over quantity.
    """
    out: List[StructuralBranch] = []
    seen: Set[str] = set()
    # Prefer else/except/none/empty over bound_check
    priority = {
        "except": 0,
        "else": 1,
        "none_check": 2,
        "empty_check": 2,
        "falsy_guard": 3,
        "match_case": 3,
        "elif": 4,
        "bound_check": 5,
    }
    ordered = sorted(branches, key=lambda b: (priority.get(b.kind, 9), b.lineno))
    for b in ordered:
        key = f"{b.path}:{b.kind}:{b.condition}:{b.enclosing}"
        if key in seen:
            continue
        if branch_is_discussed(b, discussed_text):
            continue
        if branch_has_test_signal(b, test_blob=test_blob):
            continue
        seen.add(key)
        out.append(b)
        if len(out) >= limit:
            break
    return out


def collect_branches_from_files(
    file_contents: dict[str, str],
    *,
    changed_lines_by_file: Optional[dict[str, Set[int]]] = None,
) -> List[StructuralBranch]:
    all_b: List[StructuralBranch] = []
    for path, text in (file_contents or {}).items():
        lines = None
        if changed_lines_by_file:
            lines = changed_lines_by_file.get(path)
        all_b.extend(extract_structural_branches(path, text, changed_lines=lines))
    return all_b


def parse_changed_lines_from_diff(diff: str) -> dict[str, Set[int]]:
    """Best-effort: map repo-relative paths → new-file line numbers from a unified diff."""
    out: dict[str, Set[int]] = {}
    if not diff:
        return out
    current: Optional[str] = None
    new_line = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                current = None
                continue
            if raw.startswith("b/"):
                raw = raw[2:]
            current = raw
            out.setdefault(current, set())
            continue
        if line.startswith("@@"):
            # @@ -a,b +c,d @@
            m = re.search(r"\+(\d+)", line)
            new_line = int(m.group(1)) if m else 0
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.setdefault(current, set()).add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            # context line
            new_line += 1
    return out


def _unparse(node: Optional[ast.AST]) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return type(node).__name__


def _tokens(text: str) -> Set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text or "")}
