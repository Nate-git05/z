"""Requirement checklist — decompose, bind evidence, semantic gap rescore."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
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

    def evidence_strings(self) -> List[str]:
        out = []
        out.extend(f"file:{f}" for f in self.file_hits[:5])
        out.extend(f"symbol:{s}" for s in self.symbol_hits[:5])
        out.extend(f"test:{t}" for t in self.test_hits[:5])
        out.extend(f"log:{k}" for k in self.log_hits[:5])
        out.extend(f"decision:{k}" for k in self.decision_hits[:5])
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


_PROCESS_RE = re.compile(
    r"(?i)\b(use|enable|run|with|via)\b.{0,40}\b("
    r"uncertainty|checklist|verify(?:-before-commit)?|commit\s+gate|skills?|auto-act"
    r")\b"
    r"|\b(ask|confirm|decide|review)\b.{0,40}\b(user|me|before)\b"
)
_VERIFY_RE = re.compile(
    r"(?i)\b(test|tests|verify|verification|smoke\s*test|pytest|unittest|run\s+the\s+suite)\b"
)
_DECISION_RE = re.compile(
    r"(?i)\b(confirm|decide|approve|acknowledge|ask\s+(the\s+)?user)\b"
)


def classify_requirement_kind(text: str) -> str:
    """product | process | verification | decision — process never requires source hits."""
    t = text or ""
    has_product_verb = bool(
        re.search(r"(?i)\b(implement|add|create|build|write|fix|refactor)\b", t)
    )
    # Mixed "build X and use uncertainty" → product (process is session-side)
    if _PROCESS_RE.search(t) and not has_product_verb:
        return "process"
    if _DECISION_RE.search(t) and not has_product_verb:
        return "decision"
    if _VERIFY_RE.search(t) and not re.search(
        r"(?i)\b(implement|add|create|build|write)\b.{0,20}\b(feature|endpoint|module|class)\b",
        t,
    ):
        if re.search(r"(?i)^(add|write|create)\s+tests?\b", t.strip()):
            return "verification"
        if re.search(r"(?i)\b(run|execute|smoke)\b", t) and not has_product_verb:
            return "verification"
    return "product"


def decompose_request(title: str, user_message: str) -> TaskChecklist:
    """
    Heuristic decomposition of a user request into discrete sub-requirements.

    Prefer explicit numbered/bulleted lists in the user message; otherwise split
    on conjunctions and sentence boundaries into actionable checklist items.
    """
    text = (user_message or "").strip()
    items: List[RequirementItem] = []

    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", line)
        if m:
            chunk = m.group(1).strip()
            if chunk:
                items.append(
                    RequirementItem(text=chunk, kind=classify_requirement_kind(chunk))
                )

    if not items:
        parts = re.split(r"(?:;|\.(?:\s|$)|(?:,\s*and\s+)|\band\bthen\b)", text)
        for part in parts:
            part = part.strip(" \n\t-")
            if len(part) < 8:
                continue
            if part.lower() in {"please", "thanks", "thank you"}:
                continue
            chunk = part[0].upper() + part[1:]
            items.append(
                RequirementItem(text=chunk, kind=classify_requirement_kind(chunk))
            )

    if not items and text:
        items.append(
            RequirementItem(text=text[:500], kind=classify_requirement_kind(text))
        )

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
) -> List[ItemEvidence]:
    """
    Attach concrete evidence to each checklist item.

    Process/verification/decision items use execution_log / VerificationRecord /
    user decisions — never require the phrase to appear in product source.
    """
    contents = file_contents or {}
    corpus_files = " ".join(files_changed).lower()
    corpus_body = " ".join(contents.values()).lower()[:12000]
    corpus_syms = " ".join(symbols).lower()
    corpus_tests = " ".join(test_files).lower()
    log_blob = (execution_log or "").lower()
    decisions_blob = " ".join(user_decisions or []).lower()
    verify_ok = None
    if verification is not None:
        verify_ok = bool(getattr(verification, "meaningful_pass", False))
    out: List[ItemEvidence] = []

    for item in checklist.items:
        kind = getattr(item, "kind", None) or classify_requirement_kind(item.text)
        item.kind = kind
        words = _keywords(item.text)
        ev = ItemEvidence(item_id=item.id, item_text=item.text, kind=kind)

        # Process / verification / decision — evidence from session, not source
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
            ]
            for m in markers:
                if m in log_blob or m in decisions_blob:
                    ev.log_hits.append(m)
            # Engine/session running is enough for "use uncertainty"
            if "uncertainty" in (item.text or "").lower() and (
                "uncertainty" in log_blob
                or "uncertainty tree" in log_blob
                or "verify" in log_blob
            ):
                ev.log_hits.append("session_uncertainty")
            out.append(ev)
            continue

        if kind == "verification":
            ev.verification_ok = verify_ok
            if verify_ok:
                ev.log_hits.append("verification_passed")
            elif verification is not None:
                state = getattr(verification, "state", None)
                ev.log_hits.append(f"verification_{getattr(state, 'value', state)}")
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

        # Product requirements — code/test evidence
        for w in words:
            if w in corpus_files or w in corpus_body:
                ev.keyword_hits.append(w)
            if w in corpus_syms:
                ev.symbol_hits.append(w)
            if w in corpus_tests:
                ev.test_hits.append(w)
        for f in files_changed:
            fl = f.lower()
            if any(w in fl for w in words):
                ev.file_hits.append(f)
            else:
                body = (contents.get(f) or "").lower()
                if words and sum(1 for w in words if w in body) >= max(1, len(words) // 3):
                    if f not in ev.file_hits:
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
    kind = ev.kind or "product"

    if kind == "process":
        if ev.has_process_evidence:
            return "Fully Addressed"
        # Process reqs without session evidence stay open but should not demand source
        return "Not Addressed"

    if kind == "verification":
        if ev.verification_ok is True:
            return "Fully Addressed"
        if ev.verification_ok is False:
            return "Partially Addressed"
        return "Not Addressed"

    if kind == "decision":
        if ev.decision_hits or ev.has_process_evidence:
            return "Fully Addressed"
        return "Not Addressed"

    if not words:
        if ev.has_code_evidence:
            return "Partially Addressed"
        if ev.has_test_only_evidence:
            return "Partially Addressed"
        return "Not Addressed"
    distinctive = set(ev.keyword_hits) | {
        w for w in words if any(w in t.lower() for t in ev.test_hits)
    }
    hit_ratio = len(distinctive) / max(len(words), 1)
    if ev.has_test_only_evidence:
        return "Partially Addressed" if hit_ratio >= 0.25 or ev.test_hits else "Not Addressed"
    if ev.has_code_evidence and hit_ratio >= 0.5:
        return "Fully Addressed"
    if hit_ratio >= 0.6 and ev.has_code_evidence:
        return "Fully Addressed"
    if hit_ratio >= 0.25 or ev.has_code_evidence:
        return "Partially Addressed"
    return "Not Addressed"


def rescore_checklist_with_evidence(
    checklist: TaskChecklist,
    evidence: Sequence[ItemEvidence],
) -> TaskChecklist:
    """
    Structured rescore using bound evidence.

    Process/verification/decision items never require product-source keyword hits.
    """
    by_id = {e.item_id: e for e in evidence}
    for item in checklist.items:
        ev = by_id.get(item.id) or ItemEvidence(
            item_id=item.id, item_text=item.text, kind=getattr(item, "kind", "product")
        )
        words = _keywords(item.text)
        status = _status_from_evidence(ev, words)
        kind = ev.kind or getattr(item, "kind", "product")
        if status == "Not Addressed":
            if kind == "process":
                ev.missing = (
                    f"No session/execution evidence for process requirement: {item.text}"
                )
            elif kind == "verification":
                ev.missing = f"Verification not yet satisfied for: {item.text}"
            elif kind == "decision":
                ev.missing = f"User decision still needed: {item.text}"
            else:
                ev.missing = f"No code evidence found for: {item.text}"
        elif status == "Partially Addressed":
            ev.missing = ev.missing or f"Only partial evidence for: {item.text}"
        item.status = status
    return checklist


def infer_gap_statuses_from_summary(
    checklist: TaskChecklist,
    implementation_summary: str,
) -> TaskChecklist:
    """
    Backward-compatible lexical fallback when evidence binding is unavailable.
    Prefer rescore_checklist_with_evidence in the main pipeline.
    """
    summary = (implementation_summary or "").lower()
    for item in checklist.items:
        words = _keywords(item.text)
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


def rescore_checklist_with_model(
    checklist: TaskChecklist,
    evidence: Sequence[ItemEvidence],
    *,
    model_complete: Any = None,
) -> TaskChecklist:
    """
    Optional structured Claude pass. `model_complete` is a callable(prompt)->str.

    Falls back to evidence-based rescore when no model or parse failure.
    Rule overrides still win when evidence is empty for Not Addressed.
    """
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
        "Use the evidence lists. If evidence is empty for a behavior requirement, "
        "status must be Not Addressed. Tests-only evidence is at most Partially Addressed.\n"
        f"INPUT:\n{json.dumps(payload)}"
    )
    try:
        raw = model_complete(prompt)
        if not raw:
            return checklist
        # Extract JSON object
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return checklist
        data = json.loads(m.group(0))
        by_ev = {e.item_id: e for e in evidence}
        for row in data.get("items") or []:
            item = next((i for i in checklist.items if i.id == row.get("id")), None)
            if not item:
                continue
            status = row.get("status") or item.status
            if status not in (
                "Fully Addressed",
                "Partially Addressed",
                "Not Addressed",
            ):
                continue
            ev = by_ev.get(item.id)
            # Rule override: empty evidence → Not Addressed
            if ev and not ev.has_code_evidence and not ev.test_hits and not ev.keyword_hits:
                status = "Not Addressed"
            elif ev and ev.has_test_only_evidence and status == "Fully Addressed":
                status = "Partially Addressed"
            item.status = status
            if ev is not None:
                ev.missing = (row.get("missing") or ev.missing or "").strip()
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
                "evidence": ev.evidence_strings() if ev else [],
                "missing": (ev.missing if ev and ev.missing else f"Complete: {item.text}"),
            }
        )
    return out
