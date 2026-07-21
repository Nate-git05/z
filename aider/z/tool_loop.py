"""Thin read-only tool loop — bounded scout tools before SEARCH/REPLACE.

Not a peer-agent rewrite. The model may request a few local read tools via a
``z-tool`` fence; Z runs them, budgets output, and reflects once.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from aider.z.output_budget import budget_tool_output

_TOOL_FENCE_RE = re.compile(
    r"```z-tool\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)

_ALLOWED = frozenset({"read", "grep", "glob", "ls"})


@dataclass
class ToolCall:
    name: str
    args: str


@dataclass
class ToolLoopResult:
    calls: List[ToolCall] = field(default_factory=list)
    blocks: List[str] = field(default_factory=list)
    reflect_message: str = ""
    ran: bool = False


def tool_loop_enabled() -> bool:
    raw = os.environ.get("Z_TOOL_LOOP", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def tool_loop_max() -> int:
    raw = os.environ.get("Z_TOOL_LOOP_MAX", "").strip()
    if raw.isdigit():
        return max(1, min(8, int(raw)))
    return 3


def extract_tool_calls(text: str) -> List[ToolCall]:
    if not text:
        return []
    calls: List[ToolCall] = []
    for m in _TOOL_FENCE_RE.finditer(text):
        body = m.group(1) or ""
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            name = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            if name in _ALLOWED:
                calls.append(ToolCall(name=name, args=args))
    return calls


def _safe_rel(root: Path, rel: str) -> Optional[Path]:
    rel = (rel or "").strip().strip("'\"")
    if not rel or ".." in Path(rel).parts:
        return None
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _run_read(root: Path, args: str) -> str:
    path = _safe_rel(root, args)
    if path is None:
        return f"error: invalid path `{args}`"
    if not path.is_file():
        return f"error: not a file `{args}`"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as err:
        return f"error: {err}"
    # Cap raw read before budget (extra safety)
    if len(text) > 200_000:
        text = text[:200_000] + "\n… [file truncated at 200KiB]\n"
    return text


def _run_grep(root: Path, args: str) -> str:
    # grep <pattern> [--glob PATTERN]
    tokens = args.split()
    if not tokens:
        return "error: grep requires a pattern"
    pattern = tokens[0]
    glob_pat = None
    if "--glob" in tokens:
        i = tokens.index("--glob")
        if i + 1 < len(tokens):
            glob_pat = tokens[i + 1]
    cmd = [
        "rg",
        "-n",
        "--hidden",
        "--glob",
        "!.git",
        "--glob",
        "!node_modules",
        "-m",
        "20",
        "-S",
        pattern,
    ]
    if glob_pat:
        cmd.extend(["--glob", glob_pat])
    cmd.append(str(root))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, cwd=str(root)
        )
    except FileNotFoundError:
        return "error: rg not available"
    except (OSError, subprocess.TimeoutExpired) as err:
        return f"error: {err}"
    out = (proc.stdout or "").strip() or "(no matches)"
    return out


def _run_glob(root: Path, args: str) -> str:
    pat = (args or "**/*").strip() or "**/*"
    if ".." in Path(pat).parts:
        return "error: invalid glob"
    matches: List[str] = []
    try:
        for p in sorted(root.glob(pat))[:40]:
            if p.is_file():
                try:
                    matches.append(str(p.resolve().relative_to(root.resolve())))
                except ValueError:
                    continue
    except OSError as err:
        return f"error: {err}"
    return "\n".join(matches) if matches else "(no matches)"


def _run_ls(root: Path, args: str) -> str:
    path = _safe_rel(root, args or ".")
    if path is None:
        return f"error: invalid path `{args}`"
    if not path.is_dir():
        return f"error: not a directory `{args}`"
    try:
        names = sorted(os_name for os_name in os.listdir(path))[:80]
    except OSError as err:
        return f"error: {err}"
    return "\n".join(names) if names else "(empty)"


# local import for listdir without polluting top
import os  # noqa: E402


def run_tool(root: Path, call: ToolCall) -> str:
    if call.name == "read":
        return _run_read(root, call.args)
    if call.name == "grep":
        return _run_grep(root, call.args)
    if call.name == "glob":
        return _run_glob(root, call.args)
    if call.name == "ls":
        return _run_ls(root, call.args)
    return f"error: unsupported tool `{call.name}`"


def run_tool_loop(
    response_text: str,
    *,
    root: Path | str,
    max_calls: Optional[int] = None,
) -> ToolLoopResult:
    """
    Parse and execute read-only z-tool calls from a model reply.

    Returns a result with a reflect message when tools ran.
    """
    result = ToolLoopResult()
    if not tool_loop_enabled():
        return result
    calls = extract_tool_calls(response_text)
    if not calls:
        return result

    lim = max_calls if max_calls is not None else tool_loop_max()
    root_p = Path(root)
    used = calls[:lim]
    result.calls = used
    result.ran = True

    parts = [
        "# Tool-loop results (read-only)",
        f"Executed {len(used)} tool(s)"
        + (f" (capped from {len(calls)})" if len(calls) > lim else "")
        + ". Continue with `/add` + SEARCH/REPLACE as needed.",
        "",
    ]
    for call in used:
        raw = run_tool(root_p, call)
        budgeted, path = budget_tool_output(raw, label=f"z-tool-{call.name}")
        parts.append(f"## `{call.name} {call.args}`".rstrip())
        parts.append(budgeted)
        if path:
            parts.append(f"(full output: `{path}`)")
        parts.append("")

    block = "\n".join(parts).rstrip() + "\n"
    result.blocks = [block]
    result.reflect_message = block
    return result


def strip_tool_fences(text: str) -> str:
    """Remove z-tool fences so they are not treated as prose edits."""
    if not text:
        return text
    return _TOOL_FENCE_RE.sub("", text).strip()
