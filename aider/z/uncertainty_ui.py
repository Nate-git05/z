"""DEPRECATED — demo-only uncertainty note UI.

Product `/uncertainties` uses ``aider.z.uncertainty.ui`` with the real
``UncertaintyStore`` / ``UncertaintyNode`` tree (P2). This module remains only
so older imports in theme/sandbox tests do not break; do not wire commands here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from .theme import ACCENT, ACCENT_BRIGHT, TEXT, TEXT_DIM, TEXT_MUTED


class UncertaintyTiers(str, Enum):
    """Simple confidence tiers — avoid fake-precise percentages."""

    CONFIDENT = "confident"
    NEEDS_REVIEW = "needs review"
    HIGH_RISK = "high risk"


TIER_MARKER = {
    UncertaintyTiers.CONFIDENT: ("·", TEXT_MUTED),
    UncertaintyTiers.NEEDS_REVIEW: ("▸", ACCENT),
    UncertaintyTiers.HIGH_RISK: ("‼", ACCENT_BRIGHT),
}

TIER_ORDER = [
    UncertaintyTiers.HIGH_RISK,
    UncertaintyTiers.NEEDS_REVIEW,
    UncertaintyTiers.CONFIDENT,
]


@dataclass
class UncertaintyNote:
    id: str
    title: str
    tier: UncertaintyTiers
    summary: str = ""
    files: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    suggested_fix: str = ""
    resolved: bool = False

    def tier_label(self) -> str:
        return self.tier.value


@dataclass
class UncertaintyStore:
    """Demo in-session store — not used by ``/uncertainties``."""

    notes: list[UncertaintyNote] = field(default_factory=list)

    def add(self, note: UncertaintyNote) -> None:
        self.notes.append(note)

    def get(self, note_id: str) -> UncertaintyNote | None:
        for note in self.notes:
            if note.id == note_id or note.id.startswith(note_id):
                return note
        if note_id.isdigit():
            idx = int(note_id) - 1
            active = self.active_notes()
            if 0 <= idx < len(active):
                return active[idx]
        return None

    def active_notes(self) -> list[UncertaintyNote]:
        return [n for n in self.notes if not n.resolved]

    def by_tier(self) -> dict[UncertaintyTiers, list[UncertaintyNote]]:
        grouped: dict[UncertaintyTiers, list[UncertaintyNote]] = {t: [] for t in TIER_ORDER}
        for note in self.active_notes():
            grouped.setdefault(note.tier, []).append(note)
        return grouped

    def mark_resolved(self, note_id: str) -> bool:
        note = self.get(note_id)
        if not note:
            return False
        note.resolved = True
        return True


def render_uncertainty_tree(
    store: UncertaintyStore,
    console: Console | None = None,
    *,
    pretty: bool = True,
) -> None:
    """Demo Rich tree — prefer ``aider.z.uncertainty.ui.render_tree_rich``."""
    console = console or Console()
    notes = store.active_notes()

    if not notes:
        msg = Text("No open uncertainty notes.", style=Style(color=TEXT_DIM))
        if pretty:
            console.print(msg)
        else:
            print("No open uncertainty notes.")
        return

    if not pretty:
        print("Uncertainty notes:")
        for i, note in enumerate(notes, 1):
            print(f"  {i}. [{note.tier_label()}] {note.title}")
        print("Use /uncertainties <n> to open a note.")
        return

    header = Text()
    header.append("Uncertainty notes", style=Style(color=TEXT, bold=True))
    header.append(
        f"  ({len(notes)} open)  ",
        style=Style(color=TEXT_MUTED),
    )
    header.append("select with /uncertainties <n>", style=Style(color=TEXT_DIM))
    console.print(header)
    console.print()

    index = 1
    grouped = store.by_tier()
    for tier in TIER_ORDER:
        tier_notes = grouped.get(tier) or []
        if not tier_notes:
            continue
        marker, color = TIER_MARKER[tier]
        tier_line = Text()
        tier_line.append(f"{marker} ", style=Style(color=color, bold=True))
        tier_line.append(tier.value, style=Style(color=color, bold=True))
        console.print(tier_line)

        for note in tier_notes:
            row = Text()
            row.append(f"  [{index}] ", style=Style(color=TEXT_MUTED))
            if tier == UncertaintyTiers.CONFIDENT:
                row.append("· ", style=Style(color=TEXT_MUTED))
            elif tier == UncertaintyTiers.NEEDS_REVIEW:
                row.append("[!] ", style=Style(color=ACCENT))
            else:
                row.append("[!!] ", style=Style(color=ACCENT_BRIGHT, bold=True))
            row.append(note.title, style=Style(color=TEXT))
            if note.files:
                row.append(f"  ({', '.join(note.files[:2])}", style=Style(color=TEXT_MUTED))
                if len(note.files) > 2:
                    row.append(f" +{len(note.files) - 2}", style=Style(color=TEXT_MUTED))
                row.append(")", style=Style(color=TEXT_MUTED))
            console.print(row)
            index += 1
        console.print()


NOTE_ACTIONS = [
    ("fix", "Fix it"),
    ("test", "Add a test"),
    ("explain", "Explain more"),
    ("resolve", "Mark resolved"),
]


def render_note_detail(
    note: UncertaintyNote,
    console: Console | None = None,
    *,
    pretty: bool = True,
) -> None:
    """Demo detail pane — prefer ``aider.z.uncertainty.ui.render_detail_rich``."""
    console = console or Console()
    marker, tier_color = TIER_MARKER[note.tier]

    if not pretty:
        print(f"[{note.tier_label()}] {note.title}")
        if note.summary:
            print(f"  Uncertain: {note.summary}")
        if note.files:
            print(f"  Files: {', '.join(note.files)}")
        if note.functions:
            print(f"  Functions: {', '.join(note.functions)}")
        if note.suggested_fix:
            print(f"  Suggested fix: {note.suggested_fix}")
        print("  Actions: fix | test | explain | resolve")
        return

    title = Text()
    title.append(f"{marker} ", style=Style(color=tier_color, bold=True))
    title.append(note.title, style=Style(color=TEXT, bold=True))
    title.append(f"  · {note.tier_label()}", style=Style(color=tier_color))

    body = Text()
    body.append("\nWhat's uncertain\n", style=Style(color=TEXT_DIM, bold=True))
    body.append((note.summary or "(no summary)") + "\n", style=Style(color=TEXT))

    if note.files or note.functions:
        body.append("\nInvolved\n", style=Style(color=TEXT_DIM, bold=True))
        if note.files:
            body.append("  files: ", style=Style(color=TEXT_MUTED))
            body.append(", ".join(note.files) + "\n", style=Style(color=TEXT))
        if note.functions:
            body.append("  functions: ", style=Style(color=TEXT_MUTED))
            body.append(", ".join(note.functions) + "\n", style=Style(color=TEXT))

    if note.suggested_fix:
        body.append("\nSuggested fix\n", style=Style(color=TEXT_DIM, bold=True))
        body.append(note.suggested_fix + "\n", style=Style(color=TEXT))

    body.append("\nActions\n", style=Style(color=TEXT_DIM, bold=True))
    for key, label in NOTE_ACTIONS:
        body.append(f"  /uncertainties {note.id} {key}", style=Style(color=ACCENT, bold=True))
        body.append(f"  — {label}\n", style=Style(color=TEXT_DIM))

    border = ACCENT_BRIGHT if note.tier == UncertaintyTiers.HIGH_RISK else ACCENT
    if note.tier == UncertaintyTiers.CONFIDENT:
        border = TEXT_MUTED

    console.print(
        Panel(
            body,
            title=title,
            border_style=Style(color=border),
            padding=(0, 1),
        )
    )


def prompt_note_action(
    io,
    note: UncertaintyNote,
    *,
    on_fix: Callable[[UncertaintyNote], None] | None = None,
    on_test: Callable[[UncertaintyNote], None] | None = None,
    on_explain: Callable[[UncertaintyNote], None] | None = None,
    on_resolve: Callable[[UncertaintyNote], None] | None = None,
) -> str | None:
    """Demo action prompt — product path uses ``apply_action``."""
    question = (
        f"Action for note [{note.id}] "
        "(F)ix / (T)est / (E)xplain / (R)esolve / (S)kip?"
    )
    if hasattr(io, "tool_warning"):
        io.tool_warning(question)

    res = ""
    if hasattr(io, "prompt_ask"):
        res = (io.prompt_ask("Choice", default="s") or "").strip().lower()
    if not res:
        return None

    key = res[0]
    mapping = {"f": "fix", "t": "test", "e": "explain", "r": "resolve", "s": None}
    action = mapping.get(key)
    if action == "fix" and on_fix:
        on_fix(note)
    elif action == "test" and on_test:
        on_test(note)
    elif action == "explain" and on_explain:
        on_explain(note)
    elif action == "resolve" and on_resolve:
        on_resolve(note)
    return action
