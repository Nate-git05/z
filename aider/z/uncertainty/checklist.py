"""Requirement checklist — decompose user intent, then gap-analyze after implementation."""

from __future__ import annotations

import re
import uuid
from typing import List, Optional, Sequence

from .schema import RequirementItem, TaskChecklist


def decompose_request(title: str, user_message: str) -> TaskChecklist:
    """
    Heuristic decomposition of a user request into discrete sub-requirements.

    Prefer explicit numbered/bulleted lists in the user message; otherwise split
    on conjunctions and sentence boundaries into actionable checklist items.
    """
    text = (user_message or "").strip()
    items: List[RequirementItem] = []

    # Numbered or bulleted lines
    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", line)
        if m:
            chunk = m.group(1).strip()
            if chunk:
                items.append(RequirementItem(text=chunk))

    if not items:
        # Split on semicolons / " and " / periods for multi-part asks
        parts = re.split(r"(?:;|\.(?:\s|$)|(?:,\s*and\s+)|\band\bthen\b)", text)
        for part in parts:
            part = part.strip(" \n\t-")
            if len(part) < 8:
                continue
            # Skip pure greetings
            if part.lower() in {"please", "thanks", "thank you"}:
                continue
            items.append(RequirementItem(text=part[0].upper() + part[1:]))

    if not items and text:
        items.append(RequirementItem(text=text[:500]))

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
        lines.append(f"  {i}. {item.text}")
    return "\n".join(lines)


def apply_gap_analysis(
    checklist: TaskChecklist,
    *,
    addressed_ids: Optional[Sequence[str]] = None,
    partial_ids: Optional[Sequence[str]] = None,
    statuses: Optional[dict[str, str]] = None,
) -> TaskChecklist:
    """
    Second pass: mark each item Fully / Partially / Not Addressed.

    `statuses` maps item id → status string. Alternatively pass id lists.
    """
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
            # Leave existing status if already set to something other than default
            if item.status not in (
                "Fully Addressed",
                "Partially Addressed",
                "Not Addressed",
            ):
                item.status = "Not Addressed"
    return checklist


def infer_gap_statuses_from_summary(
    checklist: TaskChecklist,
    implementation_summary: str,
) -> TaskChecklist:
    """
    Lightweight lexical check: if checklist item keywords appear in the
    implementation summary / edited file list text, mark Fully or Partially.
    Used when no model gap-pass is available.
    """
    summary = (implementation_summary or "").lower()
    for item in checklist.items:
        words = [w for w in re.findall(r"[a-z0-9_]{4,}", item.text.lower()) if w not in {
            "with", "that", "this", "from", "have", "should", "would", "could", "into", "using"
        }]
        if not words:
            continue
        hits = sum(1 for w in words if w in summary)
        ratio = hits / max(len(words), 1)
        if ratio >= 0.6:
            item.status = "Fully Addressed"
        elif ratio >= 0.25:
            item.status = "Partially Addressed"
        else:
            item.status = "Not Addressed"
    return checklist
