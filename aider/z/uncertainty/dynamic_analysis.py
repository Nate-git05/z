"""Dynamic-risk taxonomy — sanitizers for non-deterministic correctness.

One taxonomy, three rows (concurrency / memory_safety / leaks), not three
separate systems. Same fail-closed shape as established_solutions:

  1. Mechanically tag diffs by category (threading primitives, raw
     pointer/buffer ops, alloc/free)
  2. When a matching sanitizer exists for the toolchain, running it is mandatory
  3. Prefer before/after comparison under the same stress command
  4. Even a clean run is *reduced confidence*, not proof of absence
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from aider.run_cmd import run_cmd


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def sanitizer_policy(*, non_interactive: Optional[bool] = None) -> str:
    """
    ``soft`` | ``hard`` — hard makes ``tool_missing`` block commits.

    Default: hard when ``non_interactive=True`` (e.g. --yes-always) or when
    ``Z_SANITIZER_POLICY=hard``; otherwise soft (interactive honesty without teeth).
    """
    raw = (os.environ.get("Z_SANITIZER_POLICY") or "").strip().lower()
    if raw in ("soft", "hard"):
        return raw
    if non_interactive is True:
        return "hard"
    return "soft"


def sanitizer_policy_is_hard(*, non_interactive: Optional[bool] = None) -> bool:
    return sanitizer_policy(non_interactive=non_interactive) == "hard"


# ---------------------------------------------------------------------------
# Taxonomy rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DynamicRiskCategory:
    """One dynamic-risk category with a matching sanitizer family."""

    category_id: str
    title: str
    description: str
    # Match added (+) diff lines → category is relevant
    diff_regex: re.Pattern
    # Match edited paths (secondary signal)
    path_regex: re.Pattern
    # Issue-report line regexes (counted for before/after)
    issue_line_regexes: Tuple[re.Pattern, ...]
    # Human-readable sanitizer names
    sanitizer_names: Tuple[str, ...]
    # Env override for a project stress+sanitizer command
    env_cmd_keys: Tuple[str, ...]
    # Makefile / script / npm discovery hints
    makefile_targets: Tuple[str, ...]
    script_names: Tuple[str, ...]
    npm_scripts: Tuple[str, ...]
    # NodeType display string (schema NodeType value)
    node_type_value: str
    # Issue noun for summaries ("race", "memory error", "leak")
    issue_noun: str
    # Prefer these language suffixes when scoring tool relevance
    lang_suffixes: Tuple[str, ...] = ()
    # Go: append this flag to `go test` when category matches
    go_test_flag: str = ""
    confidence_label: str = (
        "dynamic analysis — this class of bug is non-deterministic; "
        "not proof of absence"
    )


def _re(pat: str, flags: int = 0) -> re.Pattern:
    return re.compile(pat, flags)


DYNAMIC_RISK_CATEGORIES: Tuple[DynamicRiskCategory, ...] = (
    DynamicRiskCategory(
        category_id="concurrency",
        title="Concurrency / data races",
        description=(
            "Diffs that touch threading, atomics, mutexes, or memory_order need "
            "ThreadSanitizer / go test -race — not just a green unit test."
        ),
        diff_regex=_re(
            r"(?x)"
            r"\b(?:std\s*::\s*)?(?:atomic|mutex|recursive_mutex|shared_mutex|"
            r"condition_variable|thread|jthread|async|future|promise|"
            r"memory_order(?:_\w+)?|atomic_thread_fence|volatile)\b"
            r"|\b(?:pthread_|stdatomic\.h|__atomic_|__sync_)\w*"
            r"|\b(?:threading|multiprocessing|concurrent\.futures|"
            r"asyncio\.(?:Lock|Queue|Event)|queue\.Queue|"
            r"multiprocessing\.(?:Lock|Queue))\b"
            r"|\b(?:sync\.(?:Mutex|RWMutex|WaitGroup|Once|Map)|go\s+func\b|"
            r"atomic\.(?:Add|Load|Store|CompareAndSwap)\w*)\b"
            r"|\b(?:java\.util\.concurrent|ReentrantLock|CountDownLatch|"
            r"volatile\s+\w+|synchronized\s*\()\b"
            r"|\b(?:Atomics\.|SharedArrayBuffer|Worker\s*\(|"
            r"Atomics\.(?:load|store|wait|notify))\b"
            r"|\b(?:parking_lot|crossbeam|std::sync::|Atomic(?:U|I)\d+|Ordering::)\b"
            r"|memory_order_(?:relaxed|acquire|release|acq_rel|seq_cst)"
            r"|\bSPSC\b|\bMPSC\b|\block[-_]?free\b|\bdata[-_]?race\b|"
            r"\brace[-_]?condition\b",
            re.IGNORECASE,
        ),
        path_regex=_re(
            r"(?i)(thread|mutex|atomic|concurrent|lockfree|lock_free|race|sync|queue)"
        ),
        issue_line_regexes=(
            _re(r"(?im)^WARNING:\s*ThreadSanitizer:\s*data\s*race\b"),
            _re(r"(?im)^WARNING:\s*DATA\s*RACE\b"),
        ),
        sanitizer_names=("ThreadSanitizer", "go test -race"),
        env_cmd_keys=("Z_RACE_DETECT_CMD", "Z_TSAN_CMD"),
        makefile_targets=("tsan", "test-tsan", "race", "sanitizer"),
        script_names=("run_tsan.sh", "scripts/tsan.sh", "tsan.sh", "scripts/race.sh"),
        npm_scripts=("test:race", "race", "test:tsan", "tsan", "stress:race"),
        node_type_value="Concurrency Race Analysis",
        issue_noun="race",
        lang_suffixes=(".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".go", ".rs"),
        go_test_flag="-race",
        confidence_label=(
            "dynamic analysis — race conditions are non-deterministic; "
            "not proof of absence"
        ),
    ),
    DynamicRiskCategory(
        category_id="memory_safety",
        title="Memory safety / invalid access",
        description=(
            "Manual pointer/buffer work in unsafe languages needs AddressSanitizer "
            "(or equivalent). A short test that happens not to crash is not evidence."
        ),
        diff_regex=_re(
            r"(?x)"
            r"\b(?:malloc|calloc|realloc|free|aligned_alloc|posix_memalign)\s*\("
            r"|\b(?:new|delete)\s*(?:\[[^\]]*\]|\w)"
            r"|\b(?:memcpy|memmove|memset|strcpy|strncpy|strcat|sprintf|gets)\s*\("
            r"|\b(?:reinterpret_cast|static_cast)\s*<"
            r"|\*\s*(?:\(\s*)?\w+(?:\s*\+\s*\w+)?\s*\)?"  # rough deref/index
            r"|\bunsafe\s*\{"
            r"|\.offset\s*\(|\.add\s*\(|from_raw_parts|NonNull::"
            r"|\b(?:ptr::|std::ptr::|slice::from_raw)"
            r"|\bBuffer\.allocUnsafe\b|\bnew\s+ArrayBuffer\b",
            re.IGNORECASE,
        ),
        path_regex=_re(
            r"(?i)(buffer|alloc|pointer|mem(?:ory)?|unsafe|arena|slab|pool)"
        ),
        issue_line_regexes=(
            _re(r"(?im)^ERROR:\s*AddressSanitizer:"),
            _re(r"(?im)^SUMMARY:\s*AddressSanitizer:"),
            _re(r"(?im)AddressSanitizer:\s*(?:heap-buffer-overflow|stack-buffer|"
                r"use-after-free|heap-use-after-free|SEGV|null-deref)"),
        ),
        sanitizer_names=("AddressSanitizer",),
        env_cmd_keys=("Z_ASAN_CMD", "Z_MEMORY_DETECT_CMD"),
        makefile_targets=("asan", "test-asan", "address-sanitizer", "sanitizer"),
        script_names=("run_asan.sh", "scripts/asan.sh", "asan.sh"),
        npm_scripts=("test:asan", "asan", "test:sanitizer", "sanitizer"),
        node_type_value="Memory Safety Analysis",
        issue_noun="memory error",
        lang_suffixes=(".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".rs"),
        confidence_label=(
            "dynamic analysis — memory errors are timing/input dependent; "
            "not proof of absence"
        ),
    ),
    DynamicRiskCategory(
        category_id="leaks",
        title="Memory leaks / unreleased allocations",
        description=(
            "Allocation-heavy changes need LeakSanitizer or valgrind. Leaks do not "
            "crash a short test run — without a dedicated watcher they are invisible."
        ),
        diff_regex=_re(
            r"(?x)"
            r"\b(?:malloc|calloc|realloc|aligned_alloc|posix_memalign|strdup|strndup)\s*\("
            r"|\bnew\s+(?:\w+|\()"
            r"|\b(?:PyMem_Malloc|PyObject_Malloc|mimalloc|jemalloc|tcmalloc)\b"
            r"|\bBox::(?:leak|from_raw)|ManuallyDrop|forget\s*\("
            r"|\b(?:std::make_unique|std::make_shared|std::unique_ptr|"
            r"std::shared_ptr)\b",
            re.IGNORECASE,
        ),
        path_regex=_re(r"(?i)(alloc|arena|pool|cache|buffer|mempool|slab)"),
        issue_line_regexes=(
            _re(r"(?im)^ERROR:\s*LeakSanitizer:"),
            _re(r"(?im)detected\s+memory\s+leaks"),
            _re(r"(?im)^\s*definitely lost:\s*[1-9]"),
            _re(r"(?im)^\s*indirectly lost:\s*[1-9]"),
            _re(r"(?im)LeakSanitizer:\s*detected\s+memory\s+leaks"),
        ),
        sanitizer_names=("LeakSanitizer", "valgrind"),
        env_cmd_keys=("Z_LSAN_CMD", "Z_LEAK_DETECT_CMD", "Z_VALGRIND_CMD"),
        makefile_targets=("lsan", "test-lsan", "leak", "valgrind", "memcheck"),
        script_names=(
            "run_lsan.sh",
            "scripts/lsan.sh",
            "lsan.sh",
            "run_valgrind.sh",
            "scripts/valgrind.sh",
        ),
        npm_scripts=("test:lsan", "lsan", "test:leak", "leak", "valgrind"),
        node_type_value="Leak Analysis",
        issue_noun="leak",
        lang_suffixes=(".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".rs"),
        confidence_label=(
            "dynamic analysis — leaks may not surface in a short run; "
            "not proof of absence"
        ),
    ),
)


def taxonomy_category_ids() -> List[str]:
    return [c.category_id for c in DYNAMIC_RISK_CATEGORIES]


def category_by_id(category_id: str) -> Optional[DynamicRiskCategory]:
    for c in DYNAMIC_RISK_CATEGORIES:
        if c.category_id == category_id:
            return c
    return None


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------


@dataclass
class DynamicRiskTag:
    category_id: str
    relevant: bool
    reasons: List[str] = field(default_factory=list)
    matched_snippets: List[str] = field(default_factory=list)


def _added_blob(diff: str) -> str:
    added = []
    for line in (diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return "\n".join(added) if added else (diff or "")


def tag_category(
    category: DynamicRiskCategory,
    diff: str = "",
    edited: Sequence[str] = (),
) -> DynamicRiskTag:
    """Mechanically tag a diff for one dynamic-risk category."""
    reasons: List[str] = []
    snippets: List[str] = []
    blob = _added_blob(diff)

    for m in category.diff_regex.finditer(blob):
        snip = m.group(0).strip()
        if snip and snip not in snippets:
            snippets.append(snip[:80])
    if snippets:
        reasons.append(
            f"{category.category_id}: diff matches "
            f"{', '.join(snippets[:6])}"
        )

    for rel in edited or ():
        if category.path_regex.search(rel.replace("\\", "/")):
            reasons.append(f"{category.category_id}: path {rel}")

    # Memory/leak categories only apply to unsafe-language files (C/C++/Rust…).
    # Pure Python/JS diffs are not AddressSanitizer / LeakSanitizer targets.
    if reasons and category.category_id in ("memory_safety", "leaks"):
        unsafe = any(
            Path(e).suffix.lower() in set(category.lang_suffixes) for e in (edited or ())
        )
        if not unsafe:
            reasons = []
            snippets = []

    return DynamicRiskTag(
        category_id=category.category_id,
        relevant=bool(reasons),
        reasons=reasons[:8],
        matched_snippets=snippets[:12],
    )


def tag_dynamic_risks(
    diff: str = "",
    edited: Sequence[str] = (),
) -> List[DynamicRiskTag]:
    """Return all matching dynamic-risk category tags (relevant only)."""
    out: List[DynamicRiskTag] = []
    for cat in DYNAMIC_RISK_CATEGORIES:
        tag = tag_category(cat, diff, edited)
        if tag.relevant:
            out.append(tag)
    return out


# ---------------------------------------------------------------------------
# Issue counting
# ---------------------------------------------------------------------------


def parse_issue_count(output: str, category: DynamicRiskCategory) -> int:
    """Best-effort count of distinct sanitizer issue reports."""
    text = output or ""
    total = 0
    for rx in category.issue_line_regexes:
        total += len(rx.findall(text))
    if total:
        return total
    # Fallback heuristics per category
    if category.category_id == "concurrency":
        generic = len(re.findall(r"(?i)\bdata\s*race\b", text))
        if generic:
            return max(1, generic // 2)
        if re.search(r"(?i)ThreadSanitizer", text) and re.search(r"(?i)race", text):
            return 1
    elif category.category_id == "memory_safety":
        if re.search(r"(?i)AddressSanitizer", text) and re.search(
            r"(?i)(ERROR|SUMMARY|overflow|use-after-free|SEGV)", text
        ):
            return 1
    elif category.category_id == "leaks":
        if re.search(r"(?i)(LeakSanitizer|definitely lost|memory leaks)", text):
            # Prefer numeric "definitely lost: N bytes" if present
            m = re.search(r"(?i)definitely lost:\s*([\d,]+)", text)
            if m and m.group(1).replace(",", "") not in ("0",):
                return 1
            if re.search(r"(?i)detected\s+memory\s+leaks", text):
                return 1
    return 0


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SanitizerTool:
    """Discoverable sanitizer / stress tool.

    Field order keeps legacy ``RaceTool(id, title, cmd, lang)`` working:
    the 4th positional arg is ``language_hint``.
    """

    tool_id: str
    title: str
    command: str
    language_hint: str = "any"
    cwd_rel: str = ""
    category_id: str = "concurrency"


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


def _npm_runner(pkg_dir: Path) -> str:
    if (pkg_dir / "bun.lockb").is_file() or (pkg_dir / "bun.lock").is_file():
        return "bun run"
    return "npm run"


def discover_tools_for_category(
    root: Path,
    category: DynamicRiskCategory,
    edited: Sequence[str] = (),
) -> List[SanitizerTool]:
    """Discover sanitizer / stress tools for one taxonomy row."""
    root = Path(root)
    tools: List[SanitizerTool] = []
    cid = category.category_id

    # Env overrides first (highest priority when set)
    for key in category.env_cmd_keys:
        env_cmd = os.environ.get(key, "").strip()
        if env_cmd:
            # Legacy concurrency id expected by older tests/callers
            if key == "Z_RACE_DETECT_CMD":
                tid = "env_race_cmd"
            elif key == "Z_TSAN_CMD":
                tid = "env_tsan_cmd"
            else:
                stem = key.lower().removeprefix("z_").removesuffix("_cmd")
                tid = f"env_{stem}"
            tools.append(
                SanitizerTool(
                    tool_id=tid,
                    title=key,
                    command=env_cmd,
                    language_hint="any",
                    category_id=cid,
                )
            )
            break

    # Go race (concurrency only)
    if category.go_test_flag and shutil.which("go"):
        go_dir = _find_go_mod_dir(root, edited)
        if go_dir is not None:
            try:
                crel = go_dir.relative_to(root.resolve()).as_posix()
            except ValueError:
                crel = ""
            tools.append(
                SanitizerTool(
                    tool_id=f"go{category.go_test_flag.replace('-', '_')}",
                    title=f"go test {category.go_test_flag}",
                    command=f"go test {category.go_test_flag} ./...",
                    language_hint="go",
                    cwd_rel=crel,
                    category_id=cid,
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
            for name in category.npm_scripts:
                if name in scripts and str(scripts[name]).strip():
                    try:
                        crel = pkg.parent.relative_to(root.resolve()).as_posix()
                    except ValueError:
                        crel = ""
                    runner = _npm_runner(pkg.parent)
                    tools.append(
                        SanitizerTool(
                            tool_id=f"npm_{name.replace(':', '_')}",
                            title=f"{runner} {name}",
                            command=f"{runner} {name}",
                            language_hint="js",
                            cwd_rel=crel,
                            category_id=cid,
                        )
                    )
                    break
    except Exception:
        pass

    # Makefile / scripts near edited files
    start_files = list(edited or ()) or ["."]
    start = (root / start_files[0]).parent if start_files[0] != "." else root
    if not start.is_dir():
        start = root

    found_make = False
    found_script = False
    cur = start
    for _ in range(6):
        if not found_make:
            mk = cur / "Makefile"
            if mk.is_file():
                try:
                    text = mk.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    text = ""
                for target in category.makefile_targets:
                    if re.search(rf"(?m)^{re.escape(target)}\s*:", text):
                        try:
                            crel = cur.relative_to(root.resolve()).as_posix()
                        except ValueError:
                            crel = ""
                        tools.append(
                            SanitizerTool(
                                tool_id=f"make_{target.replace('-', '_')}",
                                title=f"make {target}",
                                command=f"make {target}",
                                language_hint="c++",
                                cwd_rel=crel,
                                category_id=cid,
                            )
                        )
                        found_make = True
                        break
        if not found_script:
            for script_name in category.script_names:
                sp = cur / script_name
                if sp.is_file() and os.access(sp, os.X_OK):
                    try:
                        crel = cur.relative_to(root.resolve()).as_posix()
                    except ValueError:
                        crel = ""
                    tools.append(
                        SanitizerTool(
                            tool_id=f"script_{Path(script_name).stem}",
                            title=script_name,
                            command=f"./{script_name}",
                            language_hint="c++",
                            cwd_rel=crel,
                            category_id=cid,
                        )
                    )
                    found_script = True
                    break
        if cur == root or cur.parent == cur:
            break
        cur = cur.parent

    # valgrind for leaks when binary-ish C/C++ project and valgrind present
    if cid == "leaks" and shutil.which("valgrind"):
        cppish = any(
            Path(e).suffix.lower() in {".c", ".cc", ".cpp", ".cxx"}
            for e in (edited or ())
        )
        if cppish and not any(t.tool_id.startswith("make_") for t in tools):
            # Only advertise when a conventional test binary script exists
            for candidate in ("test_runner", "tests/run", "build/test"):
                if (root / candidate).exists():
                    tools.append(
                        SanitizerTool(
                            tool_id="valgrind_memcheck",
                            title="valgrind --leak-check=full",
                            command=f"valgrind --leak-check=full --error-exitcode=1 ./{candidate}",
                            language_hint="c++",
                            category_id=cid,
                        )
                    )
                    break

    seen_ids = set()
    uniq: List[SanitizerTool] = []
    for t in tools:
        if t.tool_id in seen_ids:
            continue
        seen_ids.add(t.tool_id)
        uniq.append(t)
    return uniq


# ---------------------------------------------------------------------------
# Run + before/after
# ---------------------------------------------------------------------------


@dataclass
class SanitizerRunResult:
    ran: bool = False
    command: str = ""
    cwd: str = ""
    exit_code: Optional[int] = None
    issue_count: Optional[int] = None
    # Legacy alias (concurrency tests / callers)
    race_count: Optional[int] = None
    output_excerpt: str = ""
    error: str = ""
    phase: str = ""  # before | after
    category_id: str = ""

    def __post_init__(self) -> None:
        if self.issue_count is None and self.race_count is not None:
            self.issue_count = self.race_count
        elif self.race_count is None and self.issue_count is not None:
            self.race_count = self.issue_count


@dataclass
class DynamicComparison:
    """Before/after dynamic analysis for one taxonomy category."""

    category_id: str = ""
    category_title: str = ""
    relevant: bool = False
    # Legacy: RaceComparison(concurrency_relevant=True, ...)
    concurrency_relevant: bool = False
    tag_reasons: List[str] = field(default_factory=list)
    tool: Optional[SanitizerTool] = None
    tool_available: bool = False
    before: Optional[SanitizerRunResult] = None
    after: Optional[SanitizerRunResult] = None
    # reduced | clean | no_improvement | regression | after_only | tool_missing | skipped
    outcome: str = "skipped"
    confidence_label: str = (
        "dynamic analysis — this class of bug is non-deterministic; "
        "not proof of absence"
    )
    summary: str = ""
    issue_noun: str = "issue"
    node_type_value: str = "Dynamic Analysis"
    # sanitizer-teeth: when True, tool_missing hard-blocks commit
    hard_policy: bool = False
    attempted_commands: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.concurrency_relevant:
            self.relevant = True
            if not self.category_id:
                self.category_id = "concurrency"
            if not self.category_title:
                self.category_title = "Concurrency / data races"
            if not self.issue_noun or self.issue_noun == "issue":
                self.issue_noun = "race"
            if self.node_type_value == "Dynamic Analysis":
                self.node_type_value = "Concurrency Race Analysis"
        elif self.relevant and self.category_id == "concurrency":
            self.concurrency_relevant = True

    @property
    def blocks_commit(self) -> bool:
        if self.outcome in ("no_improvement", "regression"):
            return True
        if self.outcome == "tool_missing":
            if self.hard_policy or sanitizer_policy() == "hard":
                return True
        return False

    @property
    def soft_block(self) -> bool:
        if self.outcome in ("reduced", "after_only"):
            return True
        if self.outcome == "tool_missing" and not self.blocks_commit:
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "category_id": self.category_id,
            "category_title": self.category_title,
            "relevant": self.relevant,
            "concurrency_relevant": bool(
                self.concurrency_relevant
                or (self.relevant and self.category_id == "concurrency")
            ),
            "tag_reasons": list(self.tag_reasons),
            "tool_id": self.tool.tool_id if self.tool else None,
            "tool_available": self.tool_available,
            "outcome": self.outcome,
            "confidence_label": self.confidence_label,
            "summary": self.summary,
            "issue_noun": self.issue_noun,
            "before_issues": self.before.issue_count if self.before else None,
            "after_issues": self.after.issue_count if self.after else None,
            # Back-compat keys for concurrency reflect / older consumers
            "before_races": self.before.issue_count if self.before else None,
            "after_races": self.after.issue_count if self.after else None,
            "before_command": self.before.command if self.before else None,
            "after_command": self.after.command if self.after else None,
            "node_type_value": self.node_type_value,
            "hard_policy": bool(self.hard_policy),
            "attempted_commands": list(self.attempted_commands),
            "blocks_commit": bool(self.blocks_commit),
            "soft_block": bool(self.soft_block),
        }


def run_sanitizer_tool(
    root: Path,
    tool: SanitizerTool,
    category: DynamicRiskCategory,
    *,
    phase: str = "after",
    verbose: bool = False,
    error_print=None,
) -> SanitizerRunResult:
    root = Path(root)
    cwd = root / tool.cwd_rel if tool.cwd_rel else root
    result = SanitizerRunResult(
        ran=True,
        command=tool.command,
        cwd=str(cwd),
        phase=phase,
        category_id=category.category_id,
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
        result.issue_count = None
        result.race_count = None
        return result

    text = out or ""
    result.exit_code = int(code) if code is not None else 1
    result.output_excerpt = text[-4000:] if len(text) > 4000 else text
    result.issue_count = parse_issue_count(text, category)
    result.race_count = result.issue_count
    if result.issue_count == 0 and result.exit_code not in (0, None):
        if re.search(r"(?i)(error:|FAILED|cannot find)", text):
            result.error = "sanitizer tool exited non-zero (possible build/setup failure)"
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
    tool: SanitizerTool,
    category: DynamicRiskCategory,
    edited: Sequence[str],
    *,
    verbose: bool = False,
    error_print=None,
) -> Tuple[Optional[SanitizerRunResult], SanitizerRunResult]:
    """Run detector on current tree (after), then HEAD blobs (before)."""
    root = Path(root)
    after = run_sanitizer_tool(
        root, tool, category, phase="after", verbose=verbose, error_print=error_print
    )

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

    before: Optional[SanitizerRunResult] = None
    try:
        if baseline_ok:
            before = run_sanitizer_tool(
                root,
                tool,
                category,
                phase="before",
                verbose=verbose,
                error_print=error_print,
            )
    finally:
        for path, content in backups:
            try:
                path.write_text(content, encoding="utf-8")
            except OSError:
                pass

    return before, after


def classify_outcome(
    before: Optional[SanitizerRunResult],
    after: Optional[SanitizerRunResult],
    *,
    issue_noun: str = "issue",
) -> Tuple[str, str]:
    """Return (outcome, summary) for before/after issue counts."""
    if after is None or after.issue_count is None:
        return (
            "after_only",
            f"Sanitizer did not produce a parseable after-state ({issue_noun}s).",
        )
    after_n = after.issue_count
    if before is None or before.issue_count is None:
        if after_n == 0:
            return (
                "after_only",
                f"After-state: 0 {issue_noun}(s) reported (no before baseline) — "
                "weak evidence; this class of bug is non-deterministic.",
            )
        return (
            "after_only",
            f"After-state: {after_n} {issue_noun}(s) reported (no before baseline).",
        )

    before_n = before.issue_count
    if after_n == 0 and before_n > 0:
        return (
            "clean",
            f"Before/after: {before_n} → 0 {issue_noun}(s) under the same "
            "detector command.",
        )
    if after_n == 0 and before_n == 0:
        return (
            "clean",
            f"Before/after: 0 → 0 {issue_noun}(s) — weak evidence "
            "(may not have triggered).",
        )
    if after_n < before_n:
        return (
            "reduced",
            f"Before/after: {before_n} → {after_n} {issue_noun}(s) — real progress, "
            f"but not proven clean ({after_n} remaining).",
        )
    if after_n > before_n:
        return (
            "regression",
            f"Before/after: {before_n} → {after_n} {issue_noun}(s) — "
            "regression under detector.",
        )
    return (
        "no_improvement",
        f"Before/after: {before_n} → {after_n} {issue_noun}(s) — no reduction; "
        "fix incomplete for this dynamic-risk category.",
    )


# ---------------------------------------------------------------------------
# Nodes + full analysis
# ---------------------------------------------------------------------------


def nodes_from_comparison(
    comparison: DynamicComparison,
    *,
    signals,
    files: Sequence[str] = (),
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    created_by_session: Optional[str] = None,
    created_by_user: Optional[str] = None,
):
    """Turn a DynamicComparison into uncertainty node(s)."""
    if not comparison.relevant or comparison.outcome == "skipped":
        return []
    from .detectors import _make_node
    from .schema import NodeStatus, NodeType, Tier, parse_node_type

    outcome = comparison.outcome
    tool_name = comparison.tool.title if comparison.tool else "(none)"
    before_n = comparison.before.issue_count if comparison.before else None
    after_n = comparison.after.issue_count if comparison.after else None
    noun = comparison.issue_noun
    cat = comparison.category_id

    if outcome == "clean":
        title = f"{comparison.category_title} — clean run (not proof)"
        summary = (
            f"{comparison.summary} Confidence remains reduced: this class of bug "
            "is non-deterministic."
        )
        risk = Tier.LOW
        status = NodeStatus.OPEN
    elif outcome == "tool_missing":
        title = f"{comparison.category_title} — sanitizer not run"
        summary = comparison.summary
        if comparison.blocks_commit:
            risk = Tier.HIGH
        else:
            risk = Tier.MEDIUM
        status = NodeStatus.NEEDS_HUMAN_REVIEW
    elif outcome == "reduced":
        title = f"{comparison.category_title} reduced but not cleared ({before_n}→{after_n})"
        summary = comparison.summary
        risk = Tier.MEDIUM
        status = NodeStatus.NEEDS_HUMAN_REVIEW
    elif outcome == "regression":
        title = f"{comparison.category_title} regression ({before_n}→{after_n})"
        summary = comparison.summary
        risk = Tier.HIGH
        status = NodeStatus.NEEDS_HUMAN_REVIEW
    elif outcome == "no_improvement":
        title = f"{comparison.category_title} unchanged ({before_n}→{after_n})"
        summary = comparison.summary
        risk = Tier.HIGH
        status = NodeStatus.NEEDS_HUMAN_REVIEW
    else:  # after_only
        title = f"{comparison.category_title} — after-state only"
        summary = comparison.summary
        risk = Tier.MEDIUM
        status = NodeStatus.NEEDS_HUMAN_REVIEW

    try:
        node_type = parse_node_type(comparison.node_type_value)
    except ValueError:
        node_type = NodeType.DYNAMIC_ANALYSIS

    # Populate both generic and concurrency-compat signal fields
    signals.dynamic_risk_relevant = True
    if not getattr(signals, "dynamic_risk_categories", None):
        signals.dynamic_risk_categories = []
    if cat not in signals.dynamic_risk_categories:
        signals.dynamic_risk_categories.append(cat)
    signals.sanitizer_ran = comparison.tool_available and comparison.after is not None
    signals.sanitizer_outcome = outcome
    if cat == "concurrency":
        signals.concurrency_relevant = True
        signals.race_detector_ran = signals.sanitizer_ran
        signals.race_detector_outcome = outcome
    elif cat == "memory_safety":
        signals.memory_safety_relevant = True
    elif cat == "leaks":
        signals.leak_relevant = True

    node = _make_node(
        title=title,
        node_type=node_type,
        signals=signals,
        summary=summary,
        explanation=(
            f"Dynamic-risk category: {cat} ({comparison.category_title}).\n"
            f"Reasons: {'; '.join(comparison.tag_reasons) or '(diff match)'}\n"
            f"Tool: {tool_name}\n"
            f"Outcome: {outcome}\n"
            f"Before {noun}s: {before_n}\n"
            f"After {noun}s: {after_n}\n"
            f"{comparison.confidence_label}\n\n"
            f"After output excerpt:\n"
            f"{(comparison.after.output_excerpt if comparison.after else '')[-1200:]}"
        ),
        why_uncertain=comparison.confidence_label,
        what_could_go_wrong=(
            "A plausible-looking fix can still leave races, invalid accesses, or "
            "leaks; without before/after sanitizer evidence, 'tests passed' is not proof."
        ),
        suggested_fix=(
            f"Run the project's {', '.join(category_by_id(cat).sanitizer_names if category_by_id(cat) else ['sanitizer'])} "
            "stress command on both pre-fix and post-fix trees under the same "
            "harness; require a real reduction (ideally to zero)."
            if outcome != "clean"
            else "Keep the sanitizer stress harness in CI; do not treat a single "
            "clean run as proof."
        ),
        suggested_prompt=(
            f"Dynamic analysis ({cat}/{outcome}): {comparison.summary} "
            f"Tool={tool_name}. Re-read the code paths involved; fix remaining "
            "issues or document why the sanitizer cannot run."
        ),
        files=list(files or signals.files_changed)[:8],
        task_id=task_id,
        task_title=task_title,
        created_by_session=created_by_session,
        created_by_user=created_by_user,
        status=status,
        extra_signals={
            "dynamic_analysis": True,
            "dynamic_risk_category": cat,
            "concurrency_race": cat == "concurrency",
            "memory_safety": cat == "memory_safety",
            "leak_analysis": cat == "leaks",
            "race_outcome": outcome,
            "sanitizer_outcome": outcome,
            "race_tool": tool_name,
            "race_before": before_n,
            "race_after": after_n,
            "sanitizer_before": before_n,
            "sanitizer_after": after_n,
            "race_comparison": comparison.to_dict(),
            "dynamic_comparison": comparison.to_dict(),
            "verification_blocked": comparison.blocks_commit,
        },
    )
    node.risk_tier = risk
    node.confidence_tier = Tier.LOW if outcome != "clean" else Tier.MEDIUM
    return [node]


def analyze_category(
    root: Path,
    category: DynamicRiskCategory,
    *,
    diff: str = "",
    edited: Sequence[str] = (),
    verbose: bool = False,
    error_print=None,
    skip_before: bool = False,
    non_interactive: Optional[bool] = None,
    recipe_text: str = "",
) -> DynamicComparison:
    """Full before/after pass for one taxonomy row."""
    tag = tag_category(category, diff, edited)
    hard = sanitizer_policy_is_hard(non_interactive=non_interactive)
    cmp_ = DynamicComparison(
        category_id=category.category_id,
        category_title=category.title,
        relevant=tag.relevant,
        tag_reasons=list(tag.reasons),
        confidence_label=category.confidence_label,
        issue_noun=category.issue_noun,
        node_type_value=category.node_type_value,
        hard_policy=hard,
    )
    if not tag.relevant:
        cmp_.outcome = "skipped"
        cmp_.summary = f"Diff not tagged {category.category_id}-relevant."
        return cmp_

    tools = discover_tools_for_category(root, category, edited)
    if not tools:
        # Prefer executing concrete README/SPEC recipes before soft/hard miss
        attempted: List[str] = []
        if sanitizer_recipes_enabled_local():
            try:
                from .recipe_runner import (
                    extract_sanitizer_recipes,
                    gather_recipe_text,
                    sanitizer_recipes_enabled,
                    try_run_sanitizer_recipes,
                )

                if sanitizer_recipes_enabled():
                    blob = recipe_text or gather_recipe_text(
                        root, edited=edited, extra_texts=()
                    )
                    recipes = extract_sanitizer_recipes(blob)
                    # Prefer recipes matching this category's sanitizer names
                    names = tuple(n.lower() for n in category.sanitizer_names)
                    ranked = sorted(
                        recipes,
                        key=lambda c: (
                            0
                            if any(n[:4] in c.lower() for n in names)
                            else 1
                        ),
                    )
                    if ranked:
                        rr = try_run_sanitizer_recipes(
                            root,
                            ranked,
                            verbose=verbose,
                            error_print=error_print,
                        )
                        attempted = list(rr.attempted)
                        cmp_.attempted_commands = attempted
                        if rr.ran_ok and rr.last_command:
                            # Recipe ran — treat as after-only evidence, not tool_missing
                            from .dynamic_analysis import SanitizerRunResult  # noqa: F811

                            cmp_.tool_available = True
                            cmp_.tool = SanitizerTool(
                                tool_id="recipe",
                                title="sanitizer recipe",
                                command=rr.last_command,
                                language_hint="any",
                                category_id=category.category_id,
                            )
                            cmp_.after = SanitizerRunResult(
                                ran=True,
                                command=rr.last_command,
                                exit_code=rr.last_exit_code or 0,
                                output_excerpt=rr.last_output,
                                issue_count=parse_issue_count(
                                    rr.last_output, category
                                ),
                                phase="after",
                            )
                            outcome, summary = classify_outcome(
                                None, cmp_.after, issue_noun=category.issue_noun
                            )
                            # after-only path from classify when before is None
                            cmp_.outcome = (
                                outcome if outcome != "tool_missing" else "after_only"
                            )
                            cmp_.summary = (
                                f"{summary} (via discovered recipe: {rr.last_command})"
                            )
                            return cmp_
            except Exception:
                pass

        cmp_.tool_available = False
        cmp_.outcome = "tool_missing"
        cmp_.attempted_commands = attempted
        names = ", ".join(category.sanitizer_names)
        env_hint = category.env_cmd_keys[0] if category.env_cmd_keys else "Z_*_DETECT_CMD"
        attempt_note = ""
        if attempted:
            attempt_note = (
                " Attempted recipe(s): " + "; ".join(attempted[:3]) + "."
            )
        teeth = (
            " Hard policy: commit blocked until a sanitizer runs or "
            "Z_SANITIZER_POLICY=soft / Z_FORCE_COMMIT is used."
            if hard
            else ""
        )
        cmp_.summary = (
            f"{category.title}: no sanitizer was discovered "
            f"({names}, Makefile target, script, or {env_hint}). "
            "Dynamic analysis did not run — treat as unverifiable for this "
            f"class of bug.{attempt_note}{teeth}"
        )
        return cmp_

    tool = tools[0]
    cmp_.tool = tool
    cmp_.tool_available = True

    skip = skip_before or os.environ.get("Z_SANITIZER_SKIP_BEFORE", "").strip() in (
        "1",
        "true",
        "yes",
    )
    # Legacy alias
    if not skip and category.category_id == "concurrency":
        skip = os.environ.get("Z_RACE_SKIP_BEFORE", "").strip() in ("1", "true", "yes")

    if skip:
        after = run_sanitizer_tool(
            root, tool, category, phase="after", verbose=verbose, error_print=error_print
        )
        before = None
    else:
        before, after = _run_before_after(
            root,
            tool,
            category,
            edited,
            verbose=verbose,
            error_print=error_print,
        )

    cmp_.before = before
    cmp_.after = after
    outcome, summary = classify_outcome(before, after, issue_noun=category.issue_noun)
    cmp_.outcome = outcome
    cmp_.summary = summary
    return cmp_


def sanitizer_recipes_enabled_local() -> bool:
    return _env_bool("Z_SANITIZER_RECIPES", True)


def analyze_dynamic_risks(
    root: Path,
    *,
    diff: str = "",
    edited: Sequence[str] = (),
    verbose: bool = False,
    error_print=None,
    skip_before: bool = False,
    categories: Optional[Sequence[str]] = None,
    non_interactive: Optional[bool] = None,
    recipe_text: str = "",
) -> List[DynamicComparison]:
    """
    Run every matching dynamic-risk category (or a filtered subset).

    Returns comparisons for relevant categories only (skipped omitted unless
    you need them — here we return only relevant rows).
    """
    want = set(categories) if categories else None
    results: List[DynamicComparison] = []
    for cat in DYNAMIC_RISK_CATEGORIES:
        if want is not None and cat.category_id not in want:
            continue
        tag = tag_category(cat, diff, edited)
        if not tag.relevant:
            continue
        results.append(
            analyze_category(
                root,
                cat,
                diff=diff,
                edited=edited,
                verbose=verbose,
                error_print=error_print,
                skip_before=skip_before,
                non_interactive=non_interactive,
                recipe_text=recipe_text,
            )
        )
    return results


def worst_blocking_comparison(
    comparisons: Sequence[DynamicComparison],
) -> Optional[DynamicComparison]:
    """Prefer hard-block outcomes; else first soft-block; else None."""
    hard = [c for c in comparisons if c.blocks_commit]
    if hard:
        # Prefer regression over no_improvement for messaging
        hard_sorted = sorted(
            hard, key=lambda c: 0 if c.outcome == "regression" else 1
        )
        return hard_sorted[0]
    soft = [c for c in comparisons if c.soft_block]
    return soft[0] if soft else None
