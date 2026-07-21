"""Non-interactive run contract — exit codes, outcome line, add-files auto-seed.

Fault-plan slice ``ni-contract``: one-shot ``--message`` / ``--message-file``
runs must not exit 0 after doing nothing on implement tasks, and must recover
once when the model asks to ``/add`` files instead of editing.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Set

from aider.z.task_mode import TaskMode, classify_task_mode


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def ni_require_edits_enabled() -> bool:
    return _env_bool("Z_NI_REQUIRE_EDITS", True)


def ni_auto_seed_enabled() -> bool:
    return _env_bool("Z_NI_AUTO_SEED", True)


def ni_min_reflections() -> int:
    raw = (os.environ.get("Z_NI_MIN_REFLECTIONS") or "5").strip()
    if raw.isdigit():
        return max(1, min(20, int(raw)))
    return 5


def is_non_interactive_session(io=None) -> bool:
    """True for --yes-always or non-TTY stdin (scripted / piped runs)."""
    if io is not None and getattr(io, "yes", None) is True:
        return True
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            return True
    except Exception:
        pass
    return False


def apply_ni_reflection_floor(coder) -> None:
    """Raise max_reflections to Z_NI_MIN_REFLECTIONS for NI one-shots."""
    if not is_non_interactive_session(getattr(coder, "io", None)):
        return
    floor = ni_min_reflections()
    try:
        cur = int(getattr(coder, "max_reflections", 3) or 3)
    except Exception:
        cur = 3
    if cur < floor:
        coder.max_reflections = floor


_ADD_FILES_MISS_RE = re.compile(
    r"(?is)("
    r"please\s+add\s+(?:these\s+|any\s+of\s+these\s+|the\s+)?"
    r"(?:existing\s+)?(?:files?|paths?|that\s+already\s+exist)|"
    r"please\s+add\s+any\s+of\s+these|"
    r"please\s+add\s+these\s+existing\s+files|"
    r"add\s+(?:these\s+|any\s+of\s+these\s+)?"
    r"(?:existing\s+)?(?:files?|paths?)\s+to\s+(?:the\s+)?chat|"
    r"add\s+any\s+of\s+these\s+(?:that\s+already\s+exist\s+)?"
    r"to\s+(?:the\s+)?chat|"
    r"add\s+these\s+existing\s+files\s+to\s+(?:the\s+)?chat|"
    r"before\s+i\s+(?:execute|implement|run|proceed)|"
    r"(?:files?|paths?)\s+(?:are|is)\s+not\s+in\s+(?:the\s+)?chat|"
    r"not\s+(?:currently\s+)?in\s+(?:the\s+)?chat|"
    r"once\s+you\s+(?:have\s+)?add(?:ed)?|"
    r"(?:use\s+)?/add\b|"
    r"add\s+them\s+to\s+(?:the\s+)?chat"
    r")"
)

# Paths like src/foo.c, pkg/bar.py, CMakeLists.txt
_PATH_RE = re.compile(
    r"(?:^|[\s`\"'(\[{])"
    r"("
    r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]+"
    r"|"
    r"[A-Za-z0-9_.-]+\.(?:c|cc|cpp|cxx|h|hpp|py|ts|tsx|js|jsx|go|rs|java|"
    r"rb|php|cs|kt|swift|md|txt|cmake|toml|yaml|yml|json|sh|bash)"
    r"|"
    r"CMakeLists\.txt"
    r")"
    r"(?=$|[\s`\"')\]},:;])"
)


def detect_add_files_miss(assistant_text: str) -> bool:
    """True when the model asks the human to add files instead of editing."""
    text = (assistant_text or "").strip()
    if not text:
        return False
    # SEARCH/REPLACE means it is implementing — not an add-files miss
    if "<<<<<<< SEARCH" in text or ">>>>>>> REPLACE" in text:
        return False
    return bool(_ADD_FILES_MISS_RE.search(text))


def extract_path_mentions(*texts: str, limit: int = 24) -> List[str]:
    """Extract relative-looking file paths from SPEC / assistant prose."""
    out: List[str] = []
    seen: Set[str] = set()
    for text in texts:
        if not text:
            continue
        for m in _PATH_RE.finditer(text):
            rel = m.group(1).strip().lstrip("./").replace("\\", "/")
            if not rel or rel in seen:
                continue
            # Skip obvious non-paths
            if rel.lower() in ("e.g.", "i.e.") or rel.startswith("http"):
                continue
            seen.add(rel)
            out.append(rel)
            if len(out) >= limit:
                return out
    return out


def explore_seed_candidates(
    task: str,
    root: Path | str,
    *,
    already_in_chat: Optional[Sequence[str]] = None,
    limit: int = 8,
) -> List[str]:
    """Reuse explore ranking for auto-seed (existing files only)."""
    try:
        from aider.z.explore import _rank_candidates

        root_p = Path(root)
        _kw, ranked = _rank_candidates(
            task,
            root_p,
            already_in_chat=already_in_chat,
            max_keywords=5,
            max_files=limit,
        )
        return [rel for rel, _notes in ranked[:limit]]
    except Exception:
        return []


def discover_topic_files(
    root: Path | str,
    text: str,
    *,
    limit: int = 10,
) -> List[str]:
    """
    Map vague prose like "C++ event-bus header and source files" onto real paths.

    Looks for hyphenated topics (event-bus) and spaced topics (event bus),
    then finds matching headers/sources/tests under the repo.
    """
    root_p = Path(root)
    if not root_p.is_dir() or not (text or "").strip():
        return []
    low = text.lower()
    topics: List[str] = []
    for m in re.finditer(r"\b([a-z][a-z0-9]+(?:-[a-z0-9]+)+)\b", low):
        topics.append(m.group(1))
    for m in re.finditer(
        r"\b([a-z][a-z0-9]{1,20})\s+([a-z][a-z0-9]{1,20})\b", low
    ):
        a, b = m.group(1), m.group(2)
        if a in {
            "the",
            "and",
            "for",
            "with",
            "from",
            "into",
            "current",
            "existing",
            "please",
            "these",
            "those",
            "this",
            "that",
            "add",
            "file",
            "files",
            "test",
            "header",
            "source",
            "cmake",
        }:
            continue
        if b in {
            "file",
            "files",
            "header",
            "headers",
            "source",
            "sources",
            "test",
            "tests",
            "plan",
            "chat",
        }:
            continue
        topics.append(f"{a}-{b}")
        topics.append(f"{a}_{b}")

    # Dedup preserve order
    seen_t: Set[str] = set()
    uniq_topics: List[str] = []
    for t in topics:
        if t in seen_t:
            continue
        seen_t.add(t)
        uniq_topics.append(t)

    if not uniq_topics:
        return []

    code_ext = {
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hpp",
        ".hh",
        ".py",
        ".ts",
        ".js",
        ".go",
        ".rs",
    }
    skip_dirs = {
        ".git",
        "node_modules",
        "build",
        "dist",
        ".venv",
        "venv",
        "__pycache__",
        ".z",
    }
    hits: List[str] = []
    seen_h: Set[str] = set()
    try:
        for dirpath, dirnames, filenames in os.walk(root_p):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    rel = str(path.relative_to(root_p)).replace("\\", "/")
                except ValueError:
                    continue
                if path.suffix.lower() not in code_ext and name != "CMakeLists.txt":
                    continue
                rel_l = rel.lower()
                name_l = name.lower()
                for topic in uniq_topics:
                    t_flat = topic.replace("-", "").replace("_", "")
                    n_flat = name_l.replace("-", "").replace("_", "")
                    if (
                        topic in rel_l
                        or topic.replace("-", "_") in rel_l
                        or t_flat in n_flat
                    ):
                        if rel not in seen_h:
                            seen_h.add(rel)
                            hits.append(rel)
                        break
                if len(hits) >= limit:
                    return hits
    except OSError:
        return hits
    return hits[:limit]


def collect_seed_candidates(
    coder,
    *,
    user_message: str = "",
    assistant_text: str = "",
    limit: int = 12,
) -> List[str]:
    """Merge SPEC path mentions, assistant mentions, topic discovery, explore hits."""
    root = Path(getattr(coder, "root", None) or Path.cwd())
    try:
        in_chat = list(coder.get_inchat_relative_files() or [])
    except Exception:
        in_chat = []
    in_chat_set = {str(x).replace("\\", "/") for x in in_chat}

    mentioned = extract_path_mentions(user_message, assistant_text, limit=limit)
    topics = discover_topic_files(
        root, f"{user_message}\n{assistant_text}", limit=limit
    )
    explore = explore_seed_candidates(
        user_message or assistant_text,
        root,
        already_in_chat=in_chat,
        limit=limit,
    )

    merged: List[str] = []
    seen: Set[str] = set()
    for rel in mentioned + topics + explore:
        rel = str(rel).replace("\\", "/").lstrip("./")
        if not rel or rel in seen or rel in in_chat_set:
            continue
        seen.add(rel)
        merged.append(rel)
        if len(merged) >= limit:
            break
    return merged


def auto_seed_chat(coder, candidates: Sequence[str]) -> List[str]:
    """Add existing candidate files to chat. Returns paths actually added."""
    root = Path(getattr(coder, "root", None) or Path.cwd())
    added: List[str] = []
    for rel in candidates:
        rel_n = str(rel).replace("\\", "/").lstrip("./")
        abs_path = root / rel_n
        if not abs_path.is_file():
            continue
        try:
            # Prefer coder API so maps/read-only tracking stay consistent
            if hasattr(coder, "add_rel_fname"):
                coder.add_rel_fname(rel_n)
            else:
                continue
            added.append(rel_n)
        except Exception:
            continue
    return added


def _create_new_file_hint(missing: Sequence[str]) -> str:
    if not missing:
        return ""
    listed = ", ".join(f"`{p}`" for p in list(missing)[:8])
    return (
        f"These paths do not exist yet — create them with SEARCH/REPLACE "
        f"(or the new-file edit format), do not ask to /add: {listed}."
    )


def _plan_approved(coder) -> bool:
    try:
        eng = getattr(coder, "uncertainty_engine", None)
        ctx = getattr(eng, "ctx", None)
        return bool(getattr(ctx, "plan_approved", False))
    except Exception:
        return False


def maybe_auto_seed_reflect(
    coder,
    *,
    user_message: str = "",
    assistant_text: str = "",
) -> bool:
    """
    When the model asks to /add files instead of editing: seed chat + reflect.

    Does **not** require ``--yes-always`` / ``io.yes``. Triggers when:

    - the assistant reply is an add-files miss ("please add these files…"), or
    - a plan was already approved and the reply still stalls on add-files /
      names discoverable repo paths with no SEARCH/REPLACE yet, or
    - non-interactive session with path mentions / miss

    So after the user answers Yes on the plan confirm, Z must keep going
    without needing a second global yes flag.
    """
    if not ni_auto_seed_enabled():
        return False
    if getattr(coder, "reflected_message", None):
        return False
    if getattr(coder, "_z_ni_auto_seed_done", False):
        return False

    content = assistant_text or ""
    # Already implementing — leave it alone
    if "<<<<<<< SEARCH" in content or ">>>>>>> REPLACE" in content:
        return False
    if getattr(coder, "aider_edited_files", None):
        return False

    miss = detect_add_files_miss(content)
    ni = is_non_interactive_session(getattr(coder, "io", None))
    plan_ok = _plan_approved(coder)
    paths = extract_path_mentions(user_message, content)
    candidates = collect_seed_candidates(
        coder, user_message=user_message, assistant_text=content
    )

    # None of these branches check io.yes — plan Yes / miss prose is enough.
    should = bool(
        miss
        or (ni and (paths or candidates or miss))
        or (plan_ok and (miss or paths or candidates))
    )
    if not should:
        return False
    if not miss and not paths and not candidates:
        return False

    root = Path(getattr(coder, "root", None) or Path.cwd())
    existing = [c for c in candidates if (root / c).is_file()]
    missing = [c for c in candidates if not (root / c).is_file()]

    added = auto_seed_chat(coder, existing) if existing else []
    coder._z_ni_auto_seed_done = True

    io = getattr(coder, "io", None)
    if io is not None and added:
        try:
            io.tool_output(
                "Auto-added to chat — " + ", ".join(added[:8])
            )
        except Exception:
            pass

    parts = [
        "Files were added to the chat automatically.",
        "Implement / execute the approved plan now with SEARCH/REPLACE "
        "(or new-file) edit blocks.",
        "Do not ask to /add files again — create missing paths yourself.",
    ]
    if added:
        parts.append("Added: " + ", ".join(f"`{p}`" for p in added[:10]))
    hint = _create_new_file_hint(missing)
    if hint:
        parts.append(hint)
    elif miss and not added and not missing:
        parts.append(
            "No matching repo files were found to add. Create any new files "
            "required by the SPEC with edit blocks now."
        )

    coder.reflected_message = "\n".join(parts)
    return True


@dataclass
class NiOutcome:
    exit_code: int
    edited_count: int
    verify: str
    commit: str
    gate: str
    mode: str
    reason: str = ""

    def summary_line(self) -> str:
        return (
            f"Run outcome: edited={self.edited_count} verify={self.verify} "
            f"commit={self.commit} gate={self.gate} mode={self.mode}"
            + (f" ({self.reason})" if self.reason else "")
        )


def expects_product_edits(mode: Optional[TaskMode], user_message: str = "") -> bool:
    """Implement (and verify-with-fix) expect edits; ask/review/investigate do not."""
    if mode is None:
        mode = classify_task_mode(None, user_message)
    if mode is TaskMode.IMPLEMENT:
        return True
    # VERIFY may only run tests — edits not required
    return False


def _verify_label(coder) -> str:
    rec = getattr(coder, "last_verification", None)
    if rec is None:
        eng = getattr(coder, "uncertainty_engine", None)
        if eng is not None:
            rec = getattr(getattr(eng, "ctx", None), "last_verification", None)
    if rec is None:
        return "n/a"
    state = getattr(rec, "state", None)
    if state is not None and hasattr(state, "value"):
        return str(state.value)
    return str(state or "unknown")


def _commit_label(coder) -> str:
    h = getattr(coder, "last_aider_commit_hash", None)
    if h:
        return str(h)[:12]
    if getattr(coder, "_z_gate_hold_dirty", False):
        return "blocked"
    edited = getattr(coder, "aider_edited_files", None) or ()
    if edited:
        return "none"
    return "none"


def _gate_label(coder) -> str:
    if getattr(coder, "_z_gate_hold_dirty", False):
        return "blocked"
    if not getattr(coder, "verify_commit_gate", True):
        return "skipped"
    if getattr(coder, "last_verification", None) is not None:
        return "ok" if getattr(coder, "last_aider_commit_hash", None) else "blocked"
    return "n/a"


def evaluate_ni_outcome(
    coder,
    *,
    user_message: str = "",
    task_mode: Optional[TaskMode] = None,
) -> NiOutcome:
    """
    Decide exit code + summary for a finished NI one-shot.

    Implement + zero edits → exit 1 (when Z_NI_REQUIRE_EDITS).
    Implement + edits but gate hold / no commit → exit 1.
    Non-edit modes with assistant artifact → exit 0.
    """
    mode = task_mode or getattr(coder, "task_mode", None)
    if mode is None:
        mode = classify_task_mode(None, user_message)
    mode_s = mode.value if isinstance(mode, TaskMode) else str(mode or "unknown")

    edited = set(getattr(coder, "aider_edited_files", None) or ())
    edited_count = len(edited)
    verify = _verify_label(coder)
    commit = _commit_label(coder)
    gate = _gate_label(coder)

    assistant = ""
    try:
        assistant = (getattr(coder, "partial_response_content", None) or "").strip()
    except Exception:
        assistant = ""

    need_edits = expects_product_edits(mode if isinstance(mode, TaskMode) else None, user_message)

    if not ni_require_edits_enabled():
        return NiOutcome(
            exit_code=0,
            edited_count=edited_count,
            verify=verify,
            commit=commit,
            gate=gate,
            mode=mode_s,
            reason="Z_NI_REQUIRE_EDITS=0",
        )

    if need_edits:
        if getattr(coder, "_z_edit_apply_failed", False) is True and edited_count == 0:
            return NiOutcome(
                exit_code=1,
                edited_count=0,
                verify=verify,
                commit=commit,
                gate=gate,
                mode=mode_s,
                reason="SEARCH/REPLACE proposed but nothing applied",
            )
        if edited_count == 0:
            return NiOutcome(
                exit_code=1,
                edited_count=0,
                verify=verify,
                commit=commit,
                gate=gate,
                mode=mode_s,
                reason="no product files edited",
            )
        if gate == "blocked" or commit == "blocked":
            return NiOutcome(
                exit_code=1,
                edited_count=edited_count,
                verify=verify,
                commit=commit,
                gate=gate,
                mode=mode_s,
                reason="verification gate blocked commit",
            )
        return NiOutcome(
            exit_code=0,
            edited_count=edited_count,
            verify=verify,
            commit=commit,
            gate=gate,
            mode=mode_s,
        )

    # Non-edit modes: require some assistant artifact
    if assistant or mode in (TaskMode.PLAN, TaskMode.ASK, TaskMode.INVESTIGATE, TaskMode.REVIEW):
        # PLAN / ask / investigate / review OK with reply text (even empty plan
        # still exits 0 if mode classified — but prefer nonempty when possible)
        if assistant or edited_count > 0:
            return NiOutcome(
                exit_code=0,
                edited_count=edited_count,
                verify=verify,
                commit=commit,
                gate=gate,
                mode=mode_s,
            )
        # Classified non-edit but silent — still fail closed
        return NiOutcome(
            exit_code=1,
            edited_count=0,
            verify=verify,
            commit=commit,
            gate=gate,
            mode=mode_s,
            reason="non-edit mode produced no artifact",
        )

    return NiOutcome(
        exit_code=0,
        edited_count=edited_count,
        verify=verify,
        commit=commit,
        gate=gate,
        mode=mode_s,
    )


def format_run_outcome(outcome: NiOutcome) -> str:
    return outcome.summary_line()


def finish_ni_run(io, coder, *, user_message: str = "") -> int:
    """Print Run outcome line and return process exit code."""
    outcome = evaluate_ni_outcome(
        coder,
        user_message=user_message,
        task_mode=getattr(coder, "task_mode", None),
    )
    line = format_run_outcome(outcome)
    if io is not None:
        try:
            if outcome.exit_code != 0:
                io.tool_error(line)
            else:
                io.tool_output(line)
        except Exception:
            pass
    return int(outcome.exit_code)
