"""
Skill router — second-stage apply/skip after retrieval.

Decides whether a candidate skill should be injected for the *current*
workflow step (not just once at session start). Scaffold skills are
skipped once their artifacts exist; language mismatches are skipped;
already-injected skills are not re-injected.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Set

from aider.z.paths import ensure_z_home

from .schema import (
    SKILL_KIND_PLAYBOOK,
    SKILL_KIND_SCAFFOLD,
    Skill,
)

# Verbs that mean "bootstrap something new"
_SCAFFOLD_TASK_RE = re.compile(
    r"(?i)\b(create|init|initialize|scaffold|bootstrap|new\s+project|"
    r"from\s+scratch|starter|boilerplate)\b"
)

# Verbs that mean ongoing work in an existing tree
_ONGOING_TASK_RE = re.compile(
    r"(?i)\b(add|fix|implement|refactor|update|change|migrate|test|"
    r"handle|validate|wire|connect|route|endpoint|bug|error)\b"
)

_MARKER_TO_LANG = {
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "setup.py": "python",
    "package.json": "javascript",
    "tsconfig.json": "typescript",
    "pom.xml": "java",
    "build.gradle": "java",
    "Gemfile": "ruby",
}


@dataclass
class RepoSignals:
    root: Path
    languages: Set[str] = field(default_factory=set)
    markers: Set[str] = field(default_factory=set)
    file_count: int = 0
    established: bool = False  # has real project structure


@dataclass
class RouteDecision:
    skill: Skill
    apply: bool
    reason: str
    score: float = 0.0


def collect_repo_signals(root: Path) -> RepoSignals:
    root = Path(root or os.getcwd())
    sig = RepoSignals(root=root)
    if not root.is_dir():
        return sig

    for marker, lang in _MARKER_TO_LANG.items():
        if (root / marker).exists():
            sig.markers.add(marker)
            sig.languages.add(lang)

    # Extension sampling
    counts = {"go": 0, "python": 0, "javascript": 0, "typescript": 0, "rust": 0, "html": 0}
    n = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in {".git", "node_modules", "venv", ".venv", "__pycache__"} for part in p.parts):
            continue
        n += 1
        suf = p.suffix.lower()
        if suf == ".go":
            counts["go"] += 1
        elif suf == ".py":
            counts["python"] += 1
        elif suf in {".js", ".jsx"}:
            counts["javascript"] += 1
        elif suf in {".ts", ".tsx"}:
            counts["typescript"] += 1
        elif suf == ".rs":
            counts["rust"] += 1
        elif suf in {".html", ".htm"}:
            counts["html"] += 1
        if n >= 200:
            break
    sig.file_count = n
    for lang, c in counts.items():
        if c >= 2:
            sig.languages.add(lang)
    # Established if markers exist or enough source files
    sig.established = bool(sig.markers) or n >= 5
    return sig


def task_is_scaffold_intent(task: str) -> bool:
    return bool(_SCAFFOLD_TASK_RE.search(task or ""))


def task_is_ongoing_intent(task: str) -> bool:
    return bool(_ONGOING_TASK_RE.search(task or ""))


def artifacts_satisfied(root: Path, artifacts: Sequence[str]) -> bool:
    """True if enough listed artifacts already exist (any for soft, majority preferred)."""
    arts = [a for a in (artifacts or []) if a and str(a).strip()]
    if not arts:
        return False
    root = Path(root)
    hits = 0
    for a in arts:
        p = root / a
        if p.exists():
            hits += 1
            continue
        # Allow glob-ish directory markers ("cmd/")
        if str(a).endswith("/"):
            if (root / str(a).rstrip("/")).is_dir():
                hits += 1
    # Satisfied if any strong marker exists, or majority of listed artifacts
    if hits >= 1 and len(arts) <= 2:
        return True
    return hits >= max(1, (len(arts) + 1) // 2)


def _state_path() -> Path:
    return ensure_z_home() / "skills" / "state.json"


def load_satisfaction_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return {"satisfied": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"satisfied": {}}
        data.setdefault("satisfied", {})
        return data
    except (OSError, json.JSONDecodeError):
        return {"satisfied": {}}


def mark_skill_satisfied(repo_root: Path, skill_id: str) -> None:
    if not skill_id:
        return
    state = load_satisfaction_state()
    key = str(Path(repo_root).resolve())
    bucket = state.setdefault("satisfied", {}).setdefault(key, [])
    if skill_id not in bucket:
        bucket.append(skill_id)
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def is_skill_satisfied(repo_root: Path, skill_id: str) -> bool:
    state = load_satisfaction_state()
    key = str(Path(repo_root).resolve())
    return skill_id in (state.get("satisfied") or {}).get(key, [])


def language_compatible(skill: Skill, signals: RepoSignals) -> bool:
    """False when skill languages clearly conflict with the repo."""
    skill_langs = {lng.lower() for lng in (skill.languages or []) if lng}
    if not skill_langs:
        # Infer crude language from title/tags/triggers
        blob = " ".join(
            [
                skill.title or "",
                " ".join(skill.tags or []),
                " ".join(skill.triggers or []),
            ]
        ).lower()
        for lang, hints in {
            "go": ("go", "golang"),
            "python": ("python", "django", "flask", "pytest"),
            "javascript": ("javascript", "node", "react"),
            "typescript": ("typescript", "tsx"),
            "html": ("html", "css"),
            "rust": ("rust", "cargo"),
        }.items():
            if any(h in blob for h in hints):
                skill_langs.add(lang)
    if not skill_langs:
        return True  # unknown → don't block
    if not signals.languages:
        return True  # empty repo → allow scaffolds
    # Compatible if intersection non-empty
    if skill_langs & signals.languages:
        return True
    # html-only skill against non-html backend stack → reject
    if skill_langs <= {"html"} and signals.languages & {"go", "python", "rust", "java"}:
        return False
    # go skill against python-only repo → reject
    if not (skill_langs & signals.languages):
        return False
    return True


def route_skill(
    skill: Skill,
    task: str,
    signals: RepoSignals,
    *,
    already_injected: Optional[Set[str]] = None,
    score: float = 0.0,
    min_score: float = 0.35,
) -> RouteDecision:
    """Decide apply/skip for one candidate at the current workflow checkpoint."""
    sid = skill.id or ""
    injected = already_injected or set()

    if sid and sid in injected:
        return RouteDecision(skill, False, "already injected this session", score)

    # Captured skills that failed grounding stay local but never auto-apply
    # until the user accepts them (clears needs_review).
    if getattr(skill, "needs_review", False):
        return RouteDecision(skill, False, "needs review (ungrounded capture)", score)

    if score and score < min_score:
        return RouteDecision(skill, False, f"low relevance ({score:.2f})", score)

    if not language_compatible(skill, signals):
        return RouteDecision(skill, False, "language/stack mismatch", score)

    # Stale check: if we know grounded symbols + source files, skip when gone
    if skill.grounded_symbols and skill.source_files:
        try:
            from .grounding import symbols_still_present

            _present, missing = symbols_still_present(
                skill.grounded_symbols,
                root=signals.root,
                source_files=skill.source_files,
            )
            # If a majority of grounded symbols vanished, skill is stale
            if missing and len(missing) >= max(1, (len(skill.grounded_symbols) + 1) // 2):
                return RouteDecision(
                    skill,
                    False,
                    f"stale — missing symbols: {', '.join(missing[:5])}",
                    score,
                )
        except Exception:
            pass

    kind = (skill.kind or SKILL_KIND_PLAYBOOK).lower()
    apply_once = skill.apply_once or kind == SKILL_KIND_SCAFFOLD

    if kind == SKILL_KIND_SCAFFOLD or apply_once:
        if sid and is_skill_satisfied(signals.root, sid):
            return RouteDecision(skill, False, "scaffold already satisfied (state)", score)
        if artifacts_satisfied(signals.root, skill.artifacts):
            if sid:
                mark_skill_satisfied(signals.root, sid)
            return RouteDecision(skill, False, "scaffold artifacts already exist", score)
        # Scaffold only when the current step is actually scaffolding
        if signals.established and task_is_ongoing_intent(task) and not task_is_scaffold_intent(task):
            return RouteDecision(
                skill, False, "ongoing task — scaffold not needed", score
            )
        if not task_is_scaffold_intent(task) and signals.established:
            return RouteDecision(
                skill, False, "project exists; scaffold skipped", score
            )
        return RouteDecision(skill, True, "scaffold needed for this step", score)

    # Playbook
    if signals.established is False and task_is_scaffold_intent(task):
        # Prefer not to dump playbooks during greenfield create unless strong score
        if score < 0.55:
            return RouteDecision(skill, False, "defer playbook during scaffold step", score)

    return RouteDecision(skill, True, "playbook matches current step", score)


def route_skills(
    task: str,
    candidates: Sequence[tuple[Skill, float]],
    *,
    root: Optional[Path] = None,
    already_injected: Optional[Set[str]] = None,
    limit: int = 2,
    min_score: float = 0.35,
    prefer_playbooks_when_established: bool = True,
) -> tuple[List[Skill], List[RouteDecision]]:
    """
    Route candidates for the current workflow checkpoint.

    Returns (skills_to_inject, all_decisions).
    """
    signals = collect_repo_signals(Path(root or os.getcwd()))
    decisions: List[RouteDecision] = []
    for skill, score in candidates:
        decisions.append(
            route_skill(
                skill,
                task,
                signals,
                already_injected=already_injected,
                score=score,
                min_score=min_score,
            )
        )

    approved = [d for d in decisions if d.apply]
    if prefer_playbooks_when_established and signals.established:
        # Prefer playbooks over scaffolds when both approved
        playbooks = [d for d in approved if (d.skill.kind or "") != SKILL_KIND_SCAFFOLD]
        scaffolds = [d for d in approved if (d.skill.kind or "") == SKILL_KIND_SCAFFOLD]
        if playbooks:
            approved = playbooks + scaffolds

    approved.sort(key=lambda d: d.score, reverse=True)
    inject = [d.skill for d in approved[:limit]]

    # Mark scaffolds as satisfied once we decide to apply them (so next checkpoint skips)
    for skill in inject:
        if (skill.kind or "") == SKILL_KIND_SCAFFOLD or skill.apply_once:
            # Don't mark until artifacts exist — mark after apply only if task completed
            # Here we only mark when artifacts already appear mid-session after prior apply
            if artifacts_satisfied(signals.root, skill.artifacts):
                mark_skill_satisfied(signals.root, skill.id)

    return inject, decisions
