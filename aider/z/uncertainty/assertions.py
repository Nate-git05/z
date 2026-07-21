"""Exact assertions and state-transition test generation.

Rejects shallow/permissive patterns like:
  expect(result === "round_win" || result === "win").toBeTruthy()

Prefers exact contract assertions and derives tests from transition tables
rather than inventing them opportunistically after implementation.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence, Tuple

from .schema import (
    Area,
    NodeStatus,
    NodeType,
    Tier,
    UncertaintyNode,
)


@dataclass
class Transition:
    """One row of a state machine transition table."""

    current: str
    action: str
    expected: str
    rejected: bool = False  # True when the action must be rejected

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TransitionTable:
    name: str
    transitions: List[Transition] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "transitions": [t.to_dict() for t in self.transitions],
        }


@dataclass
class WeakAssertionFinding:
    path: str
    line: int
    snippet: str
    kind: str

    def to_dict(self) -> dict:
        return asdict(self)


_WEAK_ASSERT_PATTERNS: Sequence[tuple[str, re.Pattern[str]]] = (
    (
        "alternative_or",
        re.compile(
            r"expect\s*\([^)]*(?:===|==)[^)]*(?:\|\|)[^)]*\)\s*\.\s*toBe(?:Truthy|Truthy)?\s*\("
            r"|expect\s*\(\s*[^)]*\|\|[^)]*\)\s*\.\s*toBeTruthy\s*\("
            r"|\.toBeTruthy\s*\(\s*\)"
            r"|\.toBeDefined\s*\(\s*\)"
            r"|assertTrue\s*\(\s*[^)]*(?:\|\||or)[^)]*\)"
        ),
    ),
    (
        "ts_ignore_in_test",
        re.compile(r"@ts-ignore|@ts-expect-error"),
    ),
    (
        "empty_assert",
        re.compile(r"expect\s*\(\s*true\s*\)\s*\.\s*toBe\s*\(\s*true\s*\)"),
    ),
)


# Default challenge/match table (Codex example) — used when request looks like it
_CHALLENGE_TABLE = TransitionTable(
    name="challenge_match",
    transitions=[
        Transition("Available", "Challenge", "Challenge pending"),
        Transition("Pending", "Recipient accepts", "Match active"),
        Transition("Pending", "Recipient declines", "Both available"),
        Transition("Pending", "Sender accepts", "Rejected", rejected=True),
        Transition("Pending", "Timer expires", "Both available"),
        Transition("Active", "First choice", "Waiting"),
        Transition("Active", "Duplicate choice", "Rejected", rejected=True),
        Transition("Active", "Second choice", "Round resolved"),
        Transition("Finished", "Submit choice", "Rejected", rejected=True),
    ],
)


def scan_weak_assertions(
    file_contents: dict,
) -> List[WeakAssertionFinding]:
    """Scan test file contents for permissive/shallow assertions."""
    findings: List[WeakAssertionFinding] = []
    for path, text in (file_contents or {}).items():
        if not text:
            continue
        lower = path.replace("\\", "/").lower()
        if not any(
            tok in lower
            for tok in ("test", "spec", "__tests__", "tests/")
        ):
            # Still scan if content looks like tests
            if not re.search(r"\b(expect|assert|describe|it\(|test\()", text):
                continue
        for i, line in enumerate(text.splitlines(), 1):
            for kind, pattern in _WEAK_ASSERT_PATTERNS:
                if pattern.search(line):
                    # toBeTruthy alone on exact boolean can be ok — flag alternatives harder
                    if kind == "alternative_or" and "||" not in line and "toBeTruthy" in line:
                        # Flag toBeTruthy in new tests as soft — still record
                        pass
                    findings.append(
                        WeakAssertionFinding(
                            path=path,
                            line=i,
                            snippet=line.strip()[:160],
                            kind=kind,
                        )
                    )
                    break
    return findings


def infer_transition_table(requirements: str) -> Optional[TransitionTable]:
    text = requirements or ""
    if re.search(
        r"(?i)\b(challenge|lobby|match|multiplayer|rock[\s-]?paper|best[\s-]of|"
        r"state\s*machine|round)\b",
        text,
    ):
        return TransitionTable(
            name=_CHALLENGE_TABLE.name,
            transitions=list(_CHALLENGE_TABLE.transitions),
        )
    return None


def generate_transition_tests(table: TransitionTable, *, language: str = "ts") -> str:
    """Generate exact-assertion test stubs from a transition table."""
    lines = [
        f"// Generated from transition table: {table.name}",
        "// Prefer exact object/state assertions — never a||b toBeTruthy.",
        "",
    ]
    if language in ("ts", "js", "tsx"):
        lines.append(f'describe("{table.name} transitions", () => {{')
        for t in table.transitions:
            name = f"{t.current} + {t.action} → {t.expected}"
            lines.append(f'  it("{name}", () => {{')
            lines.append(f'    const result = applyTransition("{t.current}", "{t.action}");')
            if t.rejected:
                lines.append(
                    f'    expect(result).toEqual({{ ok: false, state: "{t.current}", '
                    f'reason: expect.any(String) }});'
                )
            else:
                lines.append(
                    f'    expect(result).toEqual({{ ok: true, state: "{t.expected}" }});'
                )
            lines.append("  });")
        lines.append("});")
    else:
        lines.append(f'class Test{table.name.title().replace("_", "")}(unittest.TestCase):')
        for i, t in enumerate(table.transitions):
            method = re.sub(r"[^a-z0-9]+", "_", f"{t.current}_{t.action}".lower())
            lines.append(f"    def test_{method}_{i}(self):")
            lines.append(
                f'        result = apply_transition("{t.current}", "{t.action}")'
            )
            if t.rejected:
                lines.append('        self.assertFalse(result["ok"])')
                lines.append(f'        self.assertEqual(result["state"], "{t.current}")')
            else:
                lines.append('        self.assertTrue(result["ok"])')
                lines.append(f'        self.assertEqual(result["state"], "{t.expected}")')
    return "\n".join(lines)


def format_transition_table(table: TransitionTable) -> str:
    lines = [
        f"State transition table — {table.name}",
        "(derive tests from this table; do not invent opportunistically):",
        "",
        "  Current | Action | Expected",
        "  --- | --- | ---",
    ]
    for t in table.transitions:
        exp = t.expected + (" (reject)" if t.rejected else "")
        lines.append(f"  {t.current} | {t.action} | {exp}")
    lines.append("")
    lines.append(
        "Assert exact contracts (status, outcome, ids, scores) — never "
        "expect(a||b).toBeTruthy()."
    )
    return "\n".join(lines)


def weak_assertion_nodes(
    findings: Sequence[WeakAssertionFinding],
    *,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
) -> List[UncertaintyNode]:
    if not findings:
        return []
    files = sorted({f.path for f in findings})
    return [
        UncertaintyNode(
            title="Weak / permissive assertions in tests",
            type=NodeType.WEAK_ASSERTION,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.MEDIUM,
            summary=(
                f"{len(findings)} shallow assertion(s) allow incompatible outcomes "
                "or skip typechecking."
            ),
            explanation="\n".join(
                f"- {f.path}:{f.line} [{f.kind}] {f.snippet}" for f in findings[:12]
            ),
            files_affected=files,
            why_uncertain=(
                "Permissive assertions (toBeTruthy / a||b) green-light multiple "
                "incompatible contracts."
            ),
            what_could_go_wrong=(
                "Regressions pass the suite because the test does not enforce "
                "the precise public contract."
            ),
            suggested_fix=(
                "Replace with exact expect(result).toEqual({...}) (or assertEqual) "
                "including status, ids, scores, and error paths. Derive cases from "
                "the transition table. No @ts-ignore unless testing runtime rejection."
            ),
            status=NodeStatus.OPEN,
            area=Area.TESTS,
            task_id=task_id,
            task_title=task_title,
            created_by_session=created_by_session,
            signals={
                "weak_assertions": True,
                "count": len(findings),
            },
        )
    ]
