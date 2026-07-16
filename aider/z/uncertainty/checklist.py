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


def classify_requirement_kind(text: str) -> str:
    """
    product | process | verification | decision | documentation | quality | external_assumption

    Process/decision/docs never require product-source keyword hits.
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
) -> List[ItemEvidence]:
    """
    Attach concrete evidence to each checklist item.

    Each kind accepts only relevant evidence:
    - process → execution log / verification / git-session facts
    - verification → VerificationRecord / tests_passed
    - documentation → README/docs sections
    - quality → stress/concurrency tests + implementation symbols
    - decision → user decisions
    - product → implementation symbols + behavioral tests
    """
    contents = file_contents or {}
    corpus_files = " ".join(files_changed).lower()
    corpus_body = " ".join(contents.values()).lower()[:12000]
    corpus_syms = " ".join(symbols).lower()
    corpus_tests = " ".join(test_files).lower()
    log_blob = (execution_log or "").lower()
    decisions_blob = " ".join(user_decisions or []).lower()
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
            if touched_docs and (
                any(w in touched_blob for w in words)
                or re.search(
                    r"(?i)\b(api|allow|usage|semantics|example|redact|changelog)\b",
                    touched_blob,
                )
            ):
                ev.evidence_notes.append(f"doc:{touched_docs[0]}")
                ev.evidence_notes.append("docs_touched:true")
            elif touched_docs and re.search(
                r"(?i)readme|document|docs?|changelog", text_l
            ):
                ev.evidence_notes.append(f"doc:{touched_docs[0]}")
                ev.evidence_notes.append("docs_touched:true")
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
        # Also scan implementation files not only tests
        for f, body in contents.items():
            if _looks_doc_path(f):
                continue
            bl = body.lower()
            if words and sum(1 for w in words if w in bl) >= max(1, len(words) // 3):
                if f not in ev.file_hits and f not in test_files:
                    ev.file_hits.append(f)
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

    if kind == "documentation":
        if ev.has_doc_evidence and (ev.keyword_hits or ev.evidence_notes):
            return "Fully Addressed"
        if ev.has_doc_evidence:
            return "Partially Addressed"
        return "Not Addressed"

    if kind == "quality":
        # Same hard bar as product: file + symbol + behavioral test
        if ev.has_hard_product_evidence:
            return "Fully Addressed"
        if ev.test_hits or ev.has_code_evidence:
            return "Partially Addressed"
        return "Not Addressed"

    if kind == "external_assumption":
        if ev.log_hits:
            return "Fully Addressed"
        return "Not Addressed"

    # product (default) — Fully only with mechanical file+symbol+test evidence
    if ev.has_hard_product_evidence:
        return "Fully Addressed"
    if not words:
        if ev.has_code_evidence or ev.has_test_only_evidence:
            return "Partially Addressed"
        return "Not Addressed"
    distinctive = set(ev.keyword_hits) | {
        w for w in words if any(w in t.lower() for t in ev.test_hits)
    }
    hit_ratio = len(distinctive) / max(len(words), 1)
    if ev.has_test_only_evidence:
        return "Partially Addressed" if hit_ratio >= 0.25 or ev.test_hits else "Not Addressed"
    if hit_ratio >= 0.25 or ev.has_code_evidence or ev.test_hits:
        return "Partially Addressed"
    return "Not Addressed"


def rescore_checklist_with_evidence(
    checklist: TaskChecklist,
    evidence: Sequence[ItemEvidence],
) -> TaskChecklist:
    """
    Structured rescore using bound evidence.

    Process/verification/decision/docs items never require product-source keyword hits.
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
            elif kind == "documentation":
                ev.missing = (
                    f"No documentation file/section evidence for: {item.text}"
                )
            elif kind == "quality":
                parts = ev.missing_hard_evidence_parts()
                ev.missing = (
                    f"Quality requirement needs file+symbol+test evidence "
                    f"(missing: {', '.join(parts) or 'all'}): {item.text}"
                )
            elif kind == "external_assumption":
                ev.missing = f"External assumption unverified: {item.text}"
            else:
                parts = ev.missing_hard_evidence_parts()
                ev.missing = (
                    f"No complete evidence (need file+symbol+test; missing: "
                    f"{', '.join(parts) or 'all'}) for: {item.text}"
                )
        elif status == "Partially Addressed":
            parts = ev.missing_hard_evidence_parts()
            if kind in ("product", "quality") and parts:
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
    Deterministic evidence wins over model judgment on contradictions.
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
        f"INPUT:\n{json.dumps(payload)}"
    )
    try:
        raw = model_complete(prompt)
        if not raw:
            return checklist
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
            kind = (ev.kind if ev else getattr(item, "kind", "product")) or "product"

            # Contradiction / grounding overrides — evidence beats model
            if ev and kind in ("process", "verification", "decision"):
                grounded = _status_from_evidence(ev, _keywords(item.text))
                if grounded == "Fully Addressed":
                    status = "Fully Addressed"
                elif status == "Fully Addressed" and grounded != "Fully Addressed":
                    status = grounded
            elif ev and kind == "documentation":
                grounded = _status_from_evidence(ev, _keywords(item.text))
                if grounded == "Fully Addressed":
                    status = "Fully Addressed"
                elif not ev.has_doc_evidence:
                    status = "Not Addressed"
            elif ev and kind in ("product", "quality"):
                grounded = _status_from_evidence(ev, _keywords(item.text))
                # Mechanical file+symbol+test bar beats model Fully claims
                if status == "Fully Addressed" and grounded != "Fully Addressed":
                    status = grounded
                elif (
                    not ev.has_code_evidence
                    and not ev.test_hits
                    and not ev.keyword_hits
                ):
                    status = "Not Addressed"
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
