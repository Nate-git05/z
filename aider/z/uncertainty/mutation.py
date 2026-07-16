"""Scoped mutation check on newly-changed lines (Codex coding-quality #11).

After the suite passes, deterministically weaken a few operators/guards in the
diff's *new* lines and re-run relevant tests. Survivors ⇒ Weak Test Suite node.

Grounded pass/fail only — no LLM judgment for the mutation itself.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .edges import parse_changed_lines_from_diff
from .verify import run_test_suite

# Mutations applied only on added/changed source lines
_MUTATIONS: List[Tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(?<![<=>!])<=(?![=])"), "<", "<= → <"),
    (re.compile(r"(?<![<=>!])>=(?![=])"), ">", ">= → >"),
    (re.compile(r"(?<![<=>!])<(?![=])"), "<=", "< → <="),
    (re.compile(r"(?<![<=>!])>(?![=])"), ">=", "> → >="),
    (re.compile(r"\bis not\b"), "is", "is not → is"),
    (re.compile(r"\bnot\s+"), "", "remove not "),
]


@dataclass
class MutationResult:
    ran: bool = False
    survivors: List[dict] = field(default_factory=list)
    killed: int = 0
    attempted: int = 0
    error: str = ""


def _candidate_lines(text: str, line_nos: Sequence[int]) -> List[Tuple[int, str]]:
    lines = text.splitlines()
    out = []
    for n in line_nos:
        if 1 <= n <= len(lines):
            out.append((n, lines[n - 1]))
    return out


def _apply_one_mutation(line: str) -> Optional[Tuple[str, str]]:
    stripped = line.lstrip()
    if stripped.startswith("#") or stripped.startswith("def ") or stripped.startswith("class "):
        return None
    if "import " in stripped and stripped.startswith(("import", "from")):
        return None
    for pat, repl, label in _MUTATIONS:
        if pat.search(line):
            return pat.sub(repl, line, count=1), label
    return None


def run_mutation_check(
    root: Path,
    *,
    edited: Sequence[str],
    relevant_tests: Sequence[str],
    test_cmd: Optional[str],
    diff: str = "",
    max_mutations: int = 3,
    verbose: bool = False,
) -> MutationResult:
    """
    Mutate up to max_mutations sites in changed lines of edited Python files,
    re-run the test command, restore files. Survivors = tests still passed.
    """
    result = MutationResult()
    if not test_cmd or not edited:
        result.error = "no test command or edited files"
        return result

    root = Path(root)
    changed_map = parse_changed_lines_from_diff(diff) if diff else {}

    # Build mutation sites
    sites: List[Tuple[Path, int, str, str, str]] = []  # path, lineno, original, mutant, label
    for rel in edited:
        rel_s = str(rel).replace("\\", "/")
        if not rel_s.endswith(".py"):
            continue
        if any(p in rel_s for p in ("/tests/", "test_", "_test.py", "conftest.py")):
            # Prefer mutating implementation, not tests
            continue
        path = root / rel_s
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        line_nos = list(changed_map.get(rel_s) or [])
        if not line_nos:
            # No diff scoping — skip rather than mutate whole file
            continue
        for lineno, line in _candidate_lines(text, line_nos):
            mut = _apply_one_mutation(line)
            if not mut:
                continue
            new_line, label = mut
            if new_line == line:
                continue
            sites.append((path, lineno, line, new_line, label))
            if len(sites) >= max_mutations:
                break
        if len(sites) >= max_mutations:
            break

    if not sites:
        result.error = "no mutable sites on changed lines"
        return result

    result.ran = True
    # Optionally narrow command to relevant tests when using unittest/pytest
    cmd = test_cmd
    if relevant_tests and "pytest" in (test_cmd or ""):
        joined = " ".join(relevant_tests[:8])
        cmd = f"{test_cmd} {joined}"

    for path, lineno, original, mutant, label in sites[:max_mutations]:
        result.attempted += 1
        try:
            original_text = path.read_text(encoding="utf-8")
        except OSError as err:
            result.error = str(err)
            continue
        lines = original_text.splitlines()
        if lineno < 1 or lineno > len(lines):
            continue
        lines[lineno - 1] = mutant
        backup = path.with_suffix(path.suffix + ".zmutbak")
        try:
            shutil.copy2(path, backup)
            path.write_text("\n".join(lines) + ("\n" if original_text.endswith("\n") else ""), encoding="utf-8")
            record = run_test_suite(root, cmd, verbose=verbose)
            still_green = bool(record.exit_code == 0 and not record.zero_tests)
            if still_green:
                result.survivors.append(
                    {
                        "file": (
                            str(path.relative_to(root))
                            if str(path).startswith(str(root))
                            else str(path)
                        ),
                        "line": lineno,
                        "mutation": label,
                        "original": original.strip(),
                        "mutant": mutant.strip(),
                    }
                )
            else:
                result.killed += 1
        except Exception as err:  # noqa: BLE001
            result.error = str(err)
        finally:
            try:
                if backup.is_file():
                    shutil.move(str(backup), str(path))
                else:
                    path.write_text(original_text, encoding="utf-8")
            except OSError:
                try:
                    path.write_text(original_text, encoding="utf-8")
                except OSError:
                    pass
            try:
                if backup.is_file():
                    backup.unlink()
            except OSError:
                pass

    return result


def mutation_nodes_from_result(
    result: MutationResult,
    *,
    signals,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
):
    """Turn survivors into Weak Test Suite uncertainty nodes."""
    if not result.survivors:
        return []
    from .detectors import _make_node
    from .schema import NodeStatus, NodeType, Tier

    files = sorted({s["file"] for s in result.survivors})
    detail = "; ".join(
        f"{s['file']}:{s['line']} {s['mutation']}" for s in result.survivors[:5]
    )
    node = _make_node(
        title="Weak tests — mutations on new lines still pass",
        node_type=NodeType.WEAK_TEST,
        signals=signals,
        summary=(
            f"{len(result.survivors)} mutation(s) on changed lines survived "
            f"({result.killed} killed / {result.attempted} attempted)."
        ),
        explanation=(
            "After the suite passed, deterministic mutations on newly changed lines "
            f"still left tests green: {detail}. "
            "Happy-path tests are not adversarial enough to catch boundary mistakes."
        ),
        why_uncertain="Tests did not notice deliberate weakenings of new logic.",
        what_could_go_wrong=(
            "Off-by-one edges, inverted conditions, or removed guards can ship unnoticed."
        ),
        suggested_fix=(
            "Add a boundary/failure-path test that fails under the surviving mutation."
        ),
        suggested_prompt=(
            f"Tests did not catch these mutations: {detail}. "
            "Add adversarial tests for the boundary those mutations weaken."
        ),
        suggested_tests=[f"Kill mutation: {s['mutation']} at {s['file']}:{s['line']}" for s in result.survivors[:5]],
        files=files,
        task_id=task_id,
        task_title=task_title,
        created_by_session=created_by_session,
        created_by_user=created_by_user,
        status=NodeStatus.NEEDS_HUMAN_REVIEW,
        extra_signals={
            "mutation_survivors": result.survivors,
            "mutations_killed": result.killed,
            "mutations_attempted": result.attempted,
        },
    )
    node.risk_tier = Tier.HIGH
    node.confidence_tier = Tier.LOW
    return [node]
