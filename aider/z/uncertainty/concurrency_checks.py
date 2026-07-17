"""Concurrency-relevant diffs → race-detector verification (before/after).

Same fail-closed shape as established_solutions / package typecheck:
  1. Mechanically tag diffs that touch threading / atomics / volatile / …
  2. When a race detector exists for the toolchain, running it is mandatory
  3. Prefer before/after comparison under the same stress command — a single
     "0 races" after-state is weak (non-deterministic)
  4. Even a clean run is *reduced confidence*, not proof of absence
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from aider.run_cmd import run_cmd


# --- Diff tagging (concurrency-relevant) ------------------------------------

_CONCURRENCY_DIFF_RE = re.compile(
    r"(?x)"
    r"\b(?:std\s*::\s*)?(?:atomic|mutex|recursive_mutex|shared_mutex|condition_variable|"
    r"thread|jthread|async|future|promise|memory_order(?:_\w+)?|atomic_thread_fence|"
    r"volatile)\b"
    r"|\b(?:pthread_|stdatomic\.h|__atomic_|__sync_)\w*"
    r"|\b(?:threading|multiprocessing|concurrent\.futures|asyncio\.(?:Lock|Queue|Event)|"
    r"queue\.Queue|multiprocessing\.(?:Lock|Queue))\b"
    r"|\b(?:sync\.(?:Mutex|RWMutex|WaitGroup|Once|Map)|go\s+func\b|"
    r"atomic\.(?:Add|Load|Store|CompareAndSwap)\w*)\b"
    r"|\b(?:java\.util\.concurrent|ReentrantLock|CountDownLatch|volatile\s+\w+|"
    r"synchronized\s*\()\b"
    r"|\b(?:Atomics\.|SharedArrayBuffer|Worker\s*\(|Atomics\.(?:load|store|wait|notify))\b"
    r"|\b(?:parking_lot|crossbeam|std::sync::|Atomic(?:U|I)\d+|Ordering::)\b"
    r"|memory_order_(?:relaxed|acquire|release|acq_rel|seq_cst)"
    r"|\bSPSC\b|\bMPSC\b|\block[-_]?free\b|\bdata[-_]?race\b|\brace[-_]?condition\b",
    re.IGNORECASE,
)

_CONCURRENCY_PATH_RE = re.compile(
    r"(?i)(thread|mutex|atomic|concurrent|lockfree|lock_free|race|sync|queue)"
)

_RACE_COUNT_RE = re.compile(
    r"(?i)("
    r"WARNING:\s*ThreadSanitizer:\s*data\s*race"
    r"|ThreadSanitizer:\s*reported\s*(\d+)\s*warnings?"
    r"|Found\s*(\d+)\s*data\s*race"
    r"|DATA\s*RACE"
    r"|race\s*detected"
    r"|==================\s*\n\s*WARNING:\s*ThreadSanitizer"
    r")"
)

# One TSan report block often starts with WARNING: ThreadSanitizer: data race
_TSAN_WARNING_RE = re.compile(
    r"(?im)^WARNING:\s*ThreadSanitizer:\s*data\s*race\b"
)
_GO_RACE_RE = re.compile(r"(?im)^WARNING:\s*DATA\s*RACE\b")


@dataclass(frozen=True)
class RaceTool:
    """One discoverable race / dynamic-analysis tool."""

    tool_id: str
    title: str
    # How to invoke (may include {root} placeholder)
    command: str
    language_hint: str  # c++ | go | rust | java | python | js
    # Optional: package-relative cwd
    cwd_rel: str = ""


@dataclass
class ConcurrencyTag:
    relevant: bool
    reasons: List[str] = field(default_factory=list)
    matched_snippets: List[str] = field(default_factory=list)


@dataclass
class RaceRunResult:
    ran: bool = False
    command: str = ""
    cwd: str = ""
    exit_code: Optional[int] = None
    race_count: Optional[int] = None
    output_excerpt: str = ""
    error: str = ""
    phase: str = ""  # before | after


@dataclass
class RaceComparison:
    """Before/after dynamic analysis under the same stress command."""

    concurrency_relevant: bool = False
    tag_reasons: List[str] = field(default_factory=list)
    tool: Optional[RaceTool] = None
    tool_available: bool = False
    before: Optional[RaceRunResult] = None
    after: Optional[RaceRunResult] = None
    # reduced | clean | no_improvement | regression | after_only | tool_missing | skipped
    outcome: str = "skipped"
    # Honest confidence: never "proven absent"
    confidence_label: str = (
        "dynamic analysis — race conditions are non-deterministic; "
        "not proof of absence"
    )
    summary: str = ""

    @property
    def blocks_commit(self) -> bool:
        """Hard-block when detector available but no improvement / regression."""
        return self.outcome in ("no_improvement", "regression")

    @property
    def soft_block(self) -> bool:
        """Reviewable: remaining races after reduction, or tool missing."""
        return self.outcome in ("reduced", "tool_missing", "after_only")

    def to_dict(self) -> dict:
        return {
            "concurrency_relevant": self.concurrency_relevant,
            "tag_reasons": list(self.tag_reasons),
            "tool_id": self.tool.tool_id if self.tool else None,
            "tool_available": self.tool_available,
            "outcome": self.outcome,
            "confidence_label": self.confidence_label,
            "summary": self.summary,
            "before_races": self.before.race_count if self.before else None,
            "after_races": self.after.race_count if self.after else None,
            "before_command": self.before.command if self.before else None,
            "after_command": self.after.command if self.after else None,
        }


def tag_concurrency_relevant(
    diff: str = "",
    edited: Sequence[str] = (),
) -> ConcurrencyTag:
    """Mechanically tag diffs that touch concurrency primitives."""
    reasons: List[str] = []
    snippets: List[str] = []
    added = []
    for line in (diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    blob = "\n".join(added) if added else (diff or "")

    for m in _CONCURRENCY_DIFF_RE.finditer(blob):
        snip = m.group(0).strip()
        if snip and snip not in snippets:
            snippets.append(snip[:80])
    if snippets:
        reasons.append(f"diff touches concurrency primitives: {', '.join(snippets[:6])}")

    for rel in edited or ():
        if _CONCURRENCY_PATH_RE.search(rel.replace("\\", "/")):
            reasons.append(f"concurrency-ish path: {rel}")

    return ConcurrencyTag(
        relevant=bool(reasons),
        reasons=reasons[:8],
        matched_snippets=snippets[:12],
    )


def parse_race_count(output: str) -> int:
    """Best-effort count of distinct race reports in sanitizer output."""
    text = output or ""
    tsan = len(_TSAN_WARNING_RE.findall(text))
    go = len(_GO_RACE_RE.findall(text))
    if tsan or go:
        return tsan + go
    # Fallback: generic "DATA RACE" / "data race" hits (dedupe roughly)
    generic = len(re.findall(r"(?i)\bdata\s*race\b", text))
    if generic:
        return max(1, generic // 2)  # often appears twice per report
    if re.search(r"(?i)ThreadSanitizer", text) and re.search(
        r"(?i)race", text
    ):
        return 1
    return 0


def _has_go_mod(root: Path) -> bool:
    return (root / "go.mod").is_file() or any(root.glob("**/go.mod"))


def _find_go_mod_dir(root: Path, edited: Sequence[str]) -> Optional[Path]:
    for rel in edited:
        cur = (root / rel).resolve().parent if rel else root
        for _ in range(8):
            if (cur / "go.mod").is_file():
                return cur
            if cur == root.resolve() or cur.parent == cur:
                break
            cur = cur.parent
    if (root / "go.mod").is_file():
        return root
    return None


def discover_race_tools(
    root: Path,
    edited: Sequence[str] = (),
) -> List[RaceTool]:
    """Discover available race / TSan-style tools near the edited files."""
    root = Path(root)
    tools: List[RaceTool] = []

    # Go
    go_dir = _find_go_mod_dir(root, edited)
    if go_dir is not None and shutil.which("go"):
        try:
            crel = go_dir.relative_to(root.resolve()).as_posix()
        except ValueError:
            crel = ""
        tools.append(
            RaceTool(
                tool_id="go_race",
                title="go test -race",
                command="go test -race ./...",
                language_hint="go",
                cwd_rel=crel,
            )
        )

    # package.json scripts
    try:
        from .package_checks import find_nearest_package_json

        seen = set()
        for rel in edited or [""]:
            pkg = find_nearest_package_json(root, rel or ".")
            if not pkg:
                pkg = root / "package.json" if (root / "package.json").is_file() else None
            if not pkg or not pkg.is_file():
                continue
            key = str(pkg.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            scripts = data.get("scripts") or {}
            for name in (
                "test:race",
                "race",
                "test:tsan",
                "tsan",
                "sanitizer",
                "test:sanitizer",
                "stress:race",
            ):
                if name in scripts and str(scripts[name]).strip():
                    try:
                        crel = pkg.parent.relative_to(root.resolve()).as_posix()
                    except ValueError:
                        crel = ""
                    runner = "npm run"
                    if (pkg.parent / "bun.lockb").is_file() or (
                        pkg.parent / "bun.lock"
                    ).is_file():
                        runner = "bun run"
                    tools.append(
                        RaceTool(
                            tool_id=f"npm_{name.replace(':', '_')}",
                            title=f"{runner} {name}",
                            command=f"{runner} {name}",
                            language_hint="js",
                            cwd_rel=crel,
                        )
                    )
                    break
    except Exception:
        pass

    # CMake / Makefile TSan targets near edited C/C++ files
    cpp_edited = [
        e
        for e in (edited or ())
        if Path(e).suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"}
    ]
    if cpp_edited:
        # Walk up from first cpp file for Makefile / CMakeLists
        start = (root / cpp_edited[0]).parent
        for _ in range(6):
            for target, tool_id in (
                ("tsan", "make_tsan"),
                ("test-tsan", "make_test_tsan"),
                ("race", "make_race"),
                ("sanitizer", "make_sanitizer"),
            ):
                mk = start / "Makefile"
                if mk.is_file():
                    try:
                        text = mk.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        text = ""
                    if re.search(rf"(?m)^{re.escape(target)}\s*:", text):
                        try:
                            crel = start.relative_to(root.resolve()).as_posix()
                        except ValueError:
                            crel = ""
                        tools.append(
                            RaceTool(
                                tool_id=tool_id,
                                title=f"make {target}",
                                command=f"make {target}",
                                language_hint="c++",
                                cwd_rel=crel,
                            )
                        )
                        break
            cmake = start / "CMakeLists.txt"
            if cmake.is_file() and shutil.which("ctest"):
                # Prefer an env-documented TSAN build script if present
                for script_name in ("run_tsan.sh", "scripts/tsan.sh", "tsan.sh"):
                    sp = start / script_name
                    if sp.is_file() and os.access(sp, os.X_OK):
                        try:
                            crel = start.relative_to(root.resolve()).as_posix()
                        except ValueError:
                            crel = ""
                        tools.append(
                            RaceTool(
                                tool_id="script_tsan",
                                title=script_name,
                                command=f"./{script_name}",
                                language_hint="c++",
                                cwd_rel=crel,
                            )
                        )
                        break
            if start == root or start.parent == start:
                break
            start = start.parent

    # Explicit override for any language / custom stress+TSan harness
    env_cmd = os.environ.get("Z_RACE_DETECT_CMD", "").strip()
    if env_cmd and not any(t.tool_id == "env_race_cmd" for t in tools):
        tools.append(
            RaceTool(
                tool_id="env_race_cmd",
                title="Z_RACE_DETECT_CMD",
                command=env_cmd,
                language_hint="any",
                cwd_rel="",
            )
        )

    # Dedup by tool_id
    seen_ids = set()
    uniq: List[RaceTool] = []
    for t in tools:
        if t.tool_id in seen_ids:
            continue
        seen_ids.add(t.tool_id)
        uniq.append(t)
    return uniq


def run_race_tool(
    root: Path,
    tool: RaceTool,
    *,
    phase: str = "after",
    verbose: bool = False,
    error_print=None,
) -> RaceRunResult:
    """Execute one race-detector / stress command and parse race counts."""
    root = Path(root)
    cwd = root / tool.cwd_rel if tool.cwd_rel else root
    result = RaceRunResult(
        ran=True, command=tool.command, cwd=str(cwd), phase=phase
    )
    try:
        code, out = run_cmd(
            tool.command,
            verbose=verbose,
            error_print=error_print,
            cwd=str(cwd),
        )
    except Exception as err:  # noqa: BLE001
        result.exit_code = 1
        result.error = str(err)
        result.output_excerpt = str(err)[-3000:]
        result.race_count = None
        return result

    text = out or ""
    result.exit_code = int(code) if code is not None else 1
    result.output_excerpt = text[-4000:] if len(text) > 4000 else text
    result.race_count = parse_race_count(text)
    # TSan often exits non-zero when races found; also count from output
    if result.race_count == 0 and result.exit_code not in (0, None):
        # Non-zero without parseable races may be build failure — leave count 0
        # but record error for diagnostics
        if re.search(r"(?i)(error:|FAILED|cannot find)", text):
            result.error = "race tool exited non-zero (possible build/setup failure)"
    return result


def _git_show_file(root: Path, rel: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _run_before_after(
    root: Path,
    tool: RaceTool,
    edited: Sequence[str],
    *,
    verbose: bool = False,
    error_print=None,
) -> Tuple[Optional[RaceRunResult], RaceRunResult]:
    """
    Run detector on current tree (after), then temporarily restore HEAD blobs
    for edited files and run again (before). Always restore afterward.
    """
    root = Path(root)
    after = run_race_tool(
        root, tool, phase="after", verbose=verbose, error_print=error_print
    )

    # Collect baseline content for existing files
    backups: List[Tuple[Path, str]] = []
    baseline_ok = False
    for rel in edited:
        rel_n = rel.replace("\\", "/")
        path = root / rel_n
        if not path.is_file():
            continue
        head = _git_show_file(root, rel_n)
        if head is None:
            continue
        try:
            current = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        backups.append((path, current))
        try:
            path.write_text(head, encoding="utf-8")
            baseline_ok = True
        except OSError:
            pass

    before: Optional[RaceRunResult] = None
    try:
        if baseline_ok:
            before = run_race_tool(
                root, tool, phase="before", verbose=verbose, error_print=error_print
            )
    finally:
        for path, content in backups:
            try:
                path.write_text(content, encoding="utf-8")
            except OSError:
                pass

    return before, after


def classify_race_outcome(
    before: Optional[RaceRunResult],
    after: Optional[RaceRunResult],
) -> Tuple[str, str]:
    """
    Return (outcome, summary).

    Outcomes: clean | reduced | no_improvement | regression | after_only
    """
    if after is None or after.race_count is None:
        return "after_only", "Race detector did not produce a parseable after-state."
    after_n = after.race_count
    if before is None or before.race_count is None:
        if after_n == 0:
            return (
                "after_only",
                "After-state: 0 races reported (no before baseline) — "
                "weak evidence; races are non-deterministic.",
            )
        return (
            "after_only",
            f"After-state: {after_n} race(s) reported (no before baseline).",
        )

    before_n = before.race_count
    if after_n == 0 and before_n > 0:
        return (
            "clean",
            f"Before/after: {before_n} → 0 races under the same detector command.",
        )
    if after_n == 0 and before_n == 0:
        return (
            "clean",
            "Before/after: 0 → 0 races — weak evidence (may not have triggered).",
        )
    if after_n < before_n:
        return (
            "reduced",
            f"Before/after: {before_n} → {after_n} races — real progress, "
            f"but not proven clean ({after_n} remaining).",
        )
    if after_n > before_n:
        return (
            "regression",
            f"Before/after: {before_n} → {after_n} races — regression under detector.",
        )
    return (
        "no_improvement",
        f"Before/after: {before_n} → {after_n} races — no reduction; "
        "fix incomplete for concurrency.",
    )


def concurrency_nodes_from_comparison(
    comparison: RaceComparison,
    *,
    signals,
    files: Sequence[str] = (),
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
):
    """Turn a RaceComparison into Concurrency Race Analysis uncertainty node(s)."""
    if not comparison.concurrency_relevant or comparison.outcome == "skipped":
        return []
    from .detectors import _make_node
    from .schema import NodeStatus, NodeType, Tier

    outcome = comparison.outcome
    tool_name = comparison.tool.title if comparison.tool else "(none)"
    before_n = comparison.before.race_count if comparison.before else None
    after_n = comparison.after.race_count if comparison.after else None

    if outcome == "clean":
        title = "Concurrency dynamic analysis — clean run (not proof)"
        summary = (
            f"{comparison.summary} Confidence remains reduced: race conditions "
            "are non-deterministic."
        )
        risk = Tier.LOW
        status = NodeStatus.OPEN
    elif outcome == "tool_missing":
        title = "Concurrency change — race detector not run"
        summary = comparison.summary
        risk = Tier.MEDIUM
        status = NodeStatus.NEEDS_HUMAN_REVIEW
    elif outcome == "reduced":
        title = f"Concurrency races reduced but not cleared ({before_n}→{after_n})"
        summary = comparison.summary
        risk = Tier.MEDIUM
        status = NodeStatus.NEEDS_HUMAN_REVIEW
    elif outcome == "regression":
        title = f"Concurrency race regression ({before_n}→{after_n})"
        summary = comparison.summary
        risk = Tier.HIGH
        status = NodeStatus.NEEDS_HUMAN_REVIEW
    elif outcome == "no_improvement":
        title = f"Concurrency races unchanged ({before_n}→{after_n})"
        summary = comparison.summary
        risk = Tier.HIGH
        status = NodeStatus.NEEDS_HUMAN_REVIEW
    else:  # after_only
        title = "Concurrency dynamic analysis — after-state only"
        summary = comparison.summary
        risk = Tier.MEDIUM
        status = NodeStatus.NEEDS_HUMAN_REVIEW

    # Cap confidence via signals
    signals.concurrency_relevant = True
    signals.race_detector_ran = comparison.tool_available and comparison.after is not None
    signals.race_detector_outcome = outcome

    node = _make_node(
        title=title,
        node_type=NodeType.CONCURRENCY_RACE,
        signals=signals,
        summary=summary,
        explanation=(
            f"Concurrency-relevant change detected.\n"
            f"Reasons: {'; '.join(comparison.tag_reasons) or '(diff primitives)'}\n"
            f"Tool: {tool_name}\n"
            f"Outcome: {outcome}\n"
            f"Before races: {before_n}\n"
            f"After races: {after_n}\n"
            f"{comparison.confidence_label}\n\n"
            f"After output excerpt:\n"
            f"{(comparison.after.output_excerpt if comparison.after else '')[-1200:]}"
        ),
        why_uncertain=comparison.confidence_label,
        what_could_go_wrong=(
            "A plausible-looking atomic/memory_order fix can still leave races; "
            "without before/after sanitizer evidence, 'tests passed' is not proof."
        ),
        suggested_fix=(
            "Run the project's ThreadSanitizer / go test -race / test:race stress "
            "command on both pre-fix and post-fix trees under the same harness; "
            "require a real reduction (ideally to zero). Remaining races need "
            "follow-up (e.g. vector reallocation vs unguarded index reads)."
            if outcome != "clean"
            else "Keep stress+TSan in CI; do not treat a single clean run as proof."
        ),
        suggested_prompt=(
            f"Concurrency verification ({outcome}): {comparison.summary} "
            f"Tool={tool_name}. Re-read the shared data structures involved; "
            "fix remaining races or document why the detector cannot run."
        ),
        files=list(files or signals.files_changed)[:8],
        task_id=task_id,
        task_title=task_title,
        created_by_session=created_by_session,
        created_by_user=created_by_user,
        status=status,
        extra_signals={
            "concurrency_race": True,
            "race_outcome": outcome,
            "race_tool": tool_name,
            "race_before": before_n,
            "race_after": after_n,
            "race_comparison": comparison.to_dict(),
            "verification_blocked": comparison.blocks_commit,
        },
    )
    node.risk_tier = risk
    # Never High confidence for dynamic race analysis
    node.confidence_tier = Tier.LOW if outcome != "clean" else Tier.MEDIUM
    return [node]


def analyze_concurrency_change(
    root: Path,
    *,
    diff: str = "",
    edited: Sequence[str] = (),
    verbose: bool = False,
    error_print=None,
    skip_before: bool = False,
) -> RaceComparison:
    """
    Full concurrency verification pass for a change.

    If not concurrency-relevant → skipped.
    If relevant but no tool → tool_missing (soft gap).
    If tool exists → before/after run + outcome classification.
    """
    tag = tag_concurrency_relevant(diff, edited)
    cmp_ = RaceComparison(
        concurrency_relevant=tag.relevant,
        tag_reasons=list(tag.reasons),
    )
    if not tag.relevant:
        cmp_.outcome = "skipped"
        cmp_.summary = "Diff not tagged concurrency-relevant."
        return cmp_

    tools = discover_race_tools(root, edited)
    if not tools:
        cmp_.tool_available = False
        cmp_.outcome = "tool_missing"
        cmp_.summary = (
            "Concurrency-relevant change, but no race detector was discovered "
            "(ThreadSanitizer target, go test -race, test:race script, or "
            "Z_RACE_DETECT_CMD). Dynamic analysis did not run — treat as "
            "unverifiable for race freedom."
        )
        return cmp_

    tool = tools[0]
    cmp_.tool = tool
    cmp_.tool_available = True

    if skip_before or os.environ.get("Z_RACE_SKIP_BEFORE", "").strip() in (
        "1",
        "true",
        "yes",
    ):
        after = run_race_tool(
            root, tool, phase="after", verbose=verbose, error_print=error_print
        )
        before = None
    else:
        before, after = _run_before_after(
            root, tool, edited, verbose=verbose, error_print=error_print
        )

    cmp_.before = before
    cmp_.after = after
    outcome, summary = classify_race_outcome(before, after)
    cmp_.outcome = outcome
    cmp_.summary = summary
    return cmp_
