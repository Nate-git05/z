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
    SKILL_KIND_BUG_PATTERN,
    SKILL_KIND_PLAYBOOK,
    SKILL_KIND_SCAFFOLD,
    Skill,
)


def normalize_repo_key(root: Optional[Path | str]) -> str:
    """Stable absolute path string for comparing skill.repo_key to the live project."""
    if root is None:
        return ""
    try:
        return str(Path(root).resolve())
    except (OSError, RuntimeError, ValueError):
        return str(root)


def skill_matches_repo(skill: Skill, root: Optional[Path | str]) -> tuple[bool, str]:
    """
    Whether a skill may auto-apply in `root`.

    - shared / repo_key="*" → yes (explicitly global)
    - repo_key set → only when it matches the current resolved root
    - legacy (no repo_key): if source_files are set and none exist under
      the current root, treat as foreign and skip (stops A→B contamination
      for older captures that never stamped repo_key)
    """
    if getattr(skill, "shared", False):
        return True, "shared skill"
    key = (getattr(skill, "repo_key", None) or "").strip()
    if key in ("*", "global", "any"):
        return True, "global repo_key"
    current = normalize_repo_key(root)
    if key:
        if not current:
            return False, "skill bound to another repo (no current root)"
        if key == current:
            return True, "repo_key match"
        # Also allow if one resolves to the other (symlink / trailing slash)
        try:
            if Path(key).resolve() == Path(current).resolve():
                return True, "repo_key match"
        except (OSError, RuntimeError, ValueError):
            pass
        return False, "skill bound to a different project"

    # Legacy skills without repo_key — foreign source_files ⇒ skip
    sources = list(getattr(skill, "source_files", None) or [])
    if sources and current:
        root_path = Path(current)
        hits = 0
        for rel in sources:
            rel_s = str(rel).strip().lstrip("./")
            if not rel_s:
                continue
            # Absolute path from another machine/project
            p = Path(rel_s)
            if p.is_absolute():
                try:
                    p.relative_to(root_path)
                    if p.is_file():
                        hits += 1
                except ValueError:
                    continue
            elif (root_path / rel_s).is_file():
                hits += 1
        if hits == 0:
            return False, "legacy skill source_files missing here (foreign project)"
    return True, "unscoped legacy skill"

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

# Bug-shaped tasks — retrieve bug_pattern pool, not feature playbooks
_BUGFIX_TASK_RE = re.compile(
    r"(?i)\b("
    r"segfault|seg\s*fault|crash(?:es|ing|ed)?|race(?:\s*condition)?|"
    r"data\s*race|intermittent|flaky|hangs?|deadlock|livelock|"
    r"memory\s*leak|leaks?\b|use[- ]after[- ]free|buffer\s*overflow|"
    r"heisenbug|aslr|asan|tsan|lsan|threadsanitizer|addresssanitizer|"
    r"null\s*deref|sigsegv|abort(?:ed|s)?|corrupted?\s+memory|"
    r"fix\s+(?:the\s+)?(?:bug|crash|segfault|race|leak)"
    r")\b"
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

    # Extension sampling — >=1 is enough for small eval / greenfield repos
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
        if c >= 1:
            sig.languages.add(lang)
    # Established if markers exist or enough source files
    sig.established = bool(sig.markers) or n >= 5
    return sig


def task_is_scaffold_intent(task: str) -> bool:
    return bool(_SCAFFOLD_TASK_RE.search(task or ""))


def task_is_ongoing_intent(task: str) -> bool:
    return bool(_ONGOING_TASK_RE.search(task or ""))


def task_is_bugfix_intent(task: str) -> bool:
    """True when the task looks like diagnosing/fixing a runtime bug."""
    return bool(_BUGFIX_TASK_RE.search(task or ""))


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


def _infer_skill_langs(skill: Skill) -> Set[str]:
    skill_langs = {lng.lower() for lng in (skill.languages or []) if lng}
    if skill_langs:
        return skill_langs
    blob = " ".join(
        [
            skill.title or "",
            skill.description or "",
            " ".join(skill.tags or []),
            " ".join(skill.triggers or []),
            (skill.content or "")[:2000],
        ]
    ).lower()
    for lang, hints in {
        "go": (r"\bgo\b", r"\bgolang\b", r"\bgo\.mod\b"),
        "python": (r"\bpython\b", r"\bdjango\b", r"\bflask\b", r"\bpytest\b"),
        "javascript": (r"\bjavascript\b", r"\bnodejs\b", r"\breact\b"),
        "typescript": (r"\btypescript\b", r"\btsx\b"),
        "html": (r"\bhtml\b", r"\bcss\b"),
        "rust": (r"\brust\b", r"\bcargo\b"),
    }.items():
        if any(re.search(h, blob) for h in hints):
            skill_langs.add(lang)
    return skill_langs


def _task_languages(task: str) -> Set[str]:
    try:
        from .infer import infer_languages

        return {lng.lower() for lng in infer_languages(task or "") if lng}
    except Exception:
        return set()


def language_compatible(
    skill: Skill,
    signals: RepoSignals,
    *,
    task: str = "",
) -> bool:
    """False when skill languages clearly conflict with the repo or task.

    ``bug_pattern`` skills are exempt from hard language rejection: they carry
    a language-agnostic ``root_cause_category`` so a concurrency / memory
    pattern captured in C++ can still surface as a hypothesis in Rust/Go.
    Relevance for that kind is scored via symptom / category similarity, not
    the literal ``languages`` metadata field.
    """
    kind = (getattr(skill, "kind", None) or SKILL_KIND_PLAYBOOK).lower()
    if kind == SKILL_KIND_BUG_PATTERN:
        return True

    skill_langs = _infer_skill_langs(skill)
    task_langs = _task_languages(task)
    repo_langs = set(signals.languages or ())

    if not skill_langs:
        return True  # unknown skill language → don't block on language alone

    # Task implies a stack that conflicts with the skill → reject (even greenfield)
    if task_langs and not (skill_langs & task_langs):
        # Allow if skill is html-adjacent frontend and task is javascript/typescript
        if skill_langs <= {"html"} and task_langs & {"javascript", "typescript", "html"}:
            return True
        return False

    if not repo_langs:
        # Empty/greenfield repo: still block foreign scaffolds when task is clear
        if not task_langs:
            # No repo signal AND no task signal — don't inject a
            # language-specific scaffold the user never asked for.
            return False
        return bool(skill_langs & task_langs)

    if skill_langs & repo_langs:
        return True
    # html-only skill against non-html backend stack → reject
    if skill_langs <= {"html"} and repo_langs & {"go", "python", "rust", "java"}:
        return False
    return False


def route_skill(
    skill: Skill,
    task: str,
    signals: RepoSignals,
    *,
    already_injected: Optional[Set[str]] = None,
    score: float = 0.0,
    min_score: float = 0.40,
) -> RouteDecision:
    """Decide apply/skip for one candidate at the current workflow checkpoint."""
    sid = skill.id or ""
    injected = already_injected or set()

    if sid and sid in injected:
        return RouteDecision(skill, False, "already injected this session", score)

    # Cross-project isolation — never pull project A's playbook into B
    ok_repo, repo_reason = skill_matches_repo(skill, signals.root)
    if not ok_repo:
        return RouteDecision(skill, False, repo_reason, score)

    # Captured / quarantined skills never auto-apply until verified.
    qstate = (getattr(skill, "quality_state", None) or "").strip().lower()
    if not qstate:
        qstate = "draft" if getattr(skill, "needs_review", False) else "verified"
    if qstate in ("draft", "rejected"):
        return RouteDecision(skill, False, f"quality_state={qstate}", score)
    if getattr(skill, "needs_review", False):
        return RouteDecision(skill, False, "needs review (ungrounded capture)", score)

    if score and score < min_score:
        return RouteDecision(skill, False, f"low relevance ({score:.2f})", score)

    if not language_compatible(skill, signals, task=task):
        return RouteDecision(skill, False, "language/stack mismatch", score)

    kind = (skill.kind or SKILL_KIND_PLAYBOOK).lower()
    apply_once = skill.apply_once or kind == SKILL_KIND_SCAFFOLD

    # Stale check: playbook/scaffold only. Shared/portable bug_pattern skills
    # carry capture-repo symbols (MsgHeader, emplace_back, …) that will never
    # exist in an unrelated project — running symbols_still_present against
    # signals.root silently blocks cross-project retrieval forever.
    # Only run for project-bound skills (non-shared with a repo_key).
    portable = bool(getattr(skill, "shared", False)) or not (
        getattr(skill, "repo_key", None) or ""
    ).strip()
    skip_stale = kind == SKILL_KIND_BUG_PATTERN and portable
    if (
        not skip_stale
        and skill.grounded_symbols
        and skill.source_files
    ):
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

    # bug_pattern: only on bug-fix intent; surfaced as hypothesis (not auto-applied playbook)
    if kind == SKILL_KIND_BUG_PATTERN:
        if not task_is_bugfix_intent(task):
            return RouteDecision(
                skill, False, "bug_pattern skipped — task is not bug-fix shaped", score
            )
        # Inject as a labeled hypothesis — caller formats specially
        return RouteDecision(
            skill, True, "bug_pattern hypothesis for bug-fix task", score
        )

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
    min_score: float = 0.40,
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
    if task_is_bugfix_intent(task):
        # Prefer bug_pattern hypotheses first on bug-fix tasks
        patterns = [
            d for d in approved if (d.skill.kind or "") == SKILL_KIND_BUG_PATTERN
        ]
        others = [
            d for d in approved if (d.skill.kind or "") != SKILL_KIND_BUG_PATTERN
        ]
        if patterns:
            approved = patterns + others
    elif prefer_playbooks_when_established and signals.established:
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


def _skill_relevance_enabled() -> bool:
    """Escape hatch: Z_SKILL_RELEVANCE_CLASSIFY=0 disables the weak-model
    relevance gate, keeping today's deterministic retrieval result as-is."""
    raw = (os.environ.get("Z_SKILL_RELEVANCE_CLASSIFY") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _skill_relevance_timeout() -> float:
    """Z_SKILL_RELEVANCE_TIMEOUT seconds (default 5.0)."""
    raw = os.environ.get("Z_SKILL_RELEVANCE_TIMEOUT", "5.0")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 5.0
    return val if val > 0 else 5.0


_SKILL_RELEVANCE_SYSTEM_PROMPT = (
    "A retrieval system suggested these candidate skills/playbooks as "
    "possibly relevant to the user's message below. For each, decide if it "
    "is ACTUALLY relevant and should be shown to a coding assistant as "
    "guidance for this specific message. Respond with a comma-separated "
    "list of the relevant candidate numbers only (e.g. \"1,3\"), or the "
    "single word \"none\" if none are relevant. Nothing else — no "
    "punctuation beyond the commas, no explanation."
)


def filter_skills_by_relevance(
    skills: List[Skill], user_message: str, classifier_model
) -> List[Skill]:
    """One-shot weak-model relevance check on the (small, already-filtered)
    candidate list returned by retrieval/routing.

    Returns ``skills`` UNCHANGED on any failure/timeout/disabled/unparseable
    response — fails OPEN to today's deterministic retrieval result. A
    broken or slow model call must never silently drop skills that already
    passed real retrieval; it may only narrow the list when it successfully
    responds. Never raises.
    """
    if classifier_model is None or not skills or not _skill_relevance_enabled():
        return skills
    if not (user_message or "").strip():
        return skills

    lines = [
        f"{i}. {s.title}: {(s.description or '').strip()[:200]}"
        for i, s in enumerate(skills, start=1)
    ]
    user_content = (
        f"User's message:\n{user_message.strip()[:2000]}\n\n"
        "Candidate skills:\n" + "\n".join(lines)
    )
    messages = [
        {"role": "system", "content": _SKILL_RELEVANCE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    from aider.z.latency import join_future, submit_background

    def _call():
        return classifier_model.simple_send_with_retries(messages)

    try:
        fut = submit_background(_call)
    except Exception:
        return skills
    raw = join_future(fut, timeout=_skill_relevance_timeout())
    if not raw or not isinstance(raw, str):
        return skills

    text = raw.strip().lower()
    if text.startswith("none"):
        return []
    indices = {int(m) for m in re.findall(r"\d+", text)}
    if not indices:
        return skills  # unparseable — fail open, keep today's result
    kept = [s for i, s in enumerate(skills, start=1) if i in indices]
    return kept if kept else skills
