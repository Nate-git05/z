"""Command risk classes for shell approval (P0.5).

Structural argv parsing — not substring matching over the whole command string.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple


class CommandRiskClass(Enum):
    READ_ONLY_REPO = "read_only_repo"
    DECLARED_VERIFICATION = "declared_verification"
    DECLARED_DEP_RESTORE = "declared_dep_restore"
    LOCAL_MUTATION = "local_mutation"
    NETWORK_WRITE = "network_write"
    DESTRUCTIVE = "destructive"
    UNKNOWN = "unknown"


# Base commands that are read-only when flags stay within an allowlist
_READ_ONLY_BASE = {
    "git": {
        "status",
        "diff",
        "log",
        "show",
        "branch",
        "rev-parse",
        "ls-files",
        "describe",
        "blame",
        "grep",
        "shortlog",
        "cat-file",
        "remote",  # with no mutating subflags — see classifier
    },
    "rg": None,  # all args ok if no shell metacharacters in original
    "grep": None,
    "ag": None,
    "ack": None,
    "ls": None,
    "cat": None,
    "head": None,
    "tail": None,
    "wc": None,
    "find": None,  # conservative: disallow -delete later
    "pwd": None,
    "which": None,
    "type": None,
    # Note: echo/true/false are NOT auto-approved — they are trivial to abuse
    # as stand-ins for arbitrary shell under --yes-always tests/policy.
    "python": {"-m"},  # only specific -m modules below
    "python3": {"-m"},
}

_GIT_MUTATING = {
    "add",
    "commit",
    "push",
    "pull",
    "fetch",
    "merge",
    "rebase",
    "cherry-pick",
    "reset",
    "checkout",
    "switch",
    "clean",
    "stash",
    "tag",
    "rm",
    "mv",
    "restore",
    "worktree",
    "submodule",
    "config",
}
_GIT_DESTRUCTIVE_FLAGS = {"--hard", "--force", "-f", "--force-with-lease"}
_DESTRUCTIVE_BASE = {
    "rm",
    "rmdir",
    "dd",
    "mkfs",
    "shutdown",
    "reboot",
    "kill",
    "killall",
    "shred",
}
_NETWORK_BASE = {
    "curl",
    "wget",
    "ssh",
    "scp",
    "rsync",
    "nc",
    "ncat",
    "npm",
    "pnpm",
    "yarn",
    "pip",
    "pip3",
    "uv",
    "cargo",
    "docker",
    "gh",
    "aws",
    "gcloud",
    "az",
    "terraform",
    "kubectl",
}


@dataclass(frozen=True)
class ClassifiedCommand:
    raw: str
    argv: Tuple[str, ...]
    risk_class: CommandRiskClass
    notice: str = ""
    approval_token: str = ""


def _has_shell_metacharacters(command: str) -> bool:
    # Reject commands that rely on shell features we won't safely parse
    return bool(re.search(r"[|&;<>`$()\n]|\n", command))


def parse_argv(command: str) -> Optional[List[str]]:
    text = (command or "").strip()
    if not text or text.startswith("#"):
        return None
    if _has_shell_metacharacters(text):
        return None
    try:
        argv = shlex.split(text)
    except ValueError:
        return None
    return argv or None


def _load_declared_scripts(root: Path) -> Set[str]:
    """Project-declared verification command strings (npm scripts, make targets)."""
    declared: Set[str] = set()
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            if isinstance(scripts, dict):
                for name in scripts:
                    declared.add(f"npm run {name}")
                    declared.add(f"pnpm run {name}")
                    declared.add(f"yarn {name}")
                    declared.add(f"yarn run {name}")
                    # also allow `npm test` style for the test script
                    if name == "test":
                        declared.add("npm test")
                        declared.add("pnpm test")
                        declared.add("yarn test")
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    makefile = root / "Makefile"
    if makefile.is_file():
        try:
            for line in makefile.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r"^([A-Za-z0-9_.-]+)\s*:", line)
                if m and not m.group(1).startswith("."):
                    declared.add(f"make {m.group(1)}")
        except OSError:
            pass
    # pytest / unittest common entrypoints when tests/ or pyproject present
    if (root / "pyproject.toml").is_file() or (root / "pytest.ini").is_file() or (
        root / "tests"
    ).is_dir():
        declared.update(
            {
                "pytest",
                "python -m pytest",
                "python3 -m pytest",
                "python -m unittest",
                "python3 -m unittest",
                "python -m unittest discover -s tests -v",
                "python3 -m unittest discover -s tests -v",
            }
        )
    return declared


def _normalize_cmd_line(argv: Sequence[str]) -> str:
    return " ".join(argv).strip()


def _is_cmake_project(root: Path) -> bool:
    return (root / "CMakeLists.txt").is_file()


def _is_declared_cmake_build_or_test(argv: Sequence[str], root: Path) -> bool:
    """
    Eval Finding 3: ``cmake --build`` / ``ctest`` must auto-approve when the
    repo is a CMake project — otherwise NI self-verification is blind
    (configure may run, compile never does, ctest reports Not Run).
    """
    if not argv or not _is_cmake_project(root):
        return False
    base = Path(argv[0]).name.lower()
    if base == "cmake":
        args = [a for a in argv[1:]]
        if not args:
            return False
        # cmake --build <dir> [...]
        if args[0] == "--build":
            return True
        # cmake -S . -B build  /  cmake -B build  /  cmake --fresh -S … -B …
        if "-S" in args or "-B" in args:
            return True
        # Disallow arbitrary -P script / --install of system paths etc.
        if args[0] in ("--install", "-P", "--graphviz"):
            return False
        return False
    if base == "ctest":
        # ctest --test-dir build --output-on-failure  (and plain ctest)
        return True
    # Generator-native build inside the usual out-of-source dir
    if base in {"ninja", "make"} and len(argv) >= 1:
        # make/ninja with -C build, or cwd already build/ (caller uses cwd=root)
        if "-C" in argv:
            return True
        # bare `ninja` / `make` only when build/ exists as cmake tree
        build = root / "build"
        if build.is_dir() and (
            (build / "build.ninja").is_file()
            or (build / "Makefile").is_file()
            or (build / "CMakeCache.txt").is_file()
        ):
            return len(argv) <= 6  # allow -jN / targets, not pipelines
    return False


def _is_declared_verification(argv: Sequence[str], root: Path) -> bool:
    if _is_declared_cmake_build_or_test(argv, root):
        return True
    declared = _load_declared_scripts(root)
    line = _normalize_cmd_line(argv)
    if line in declared:
        return True
    # prefix match for `npm run foo -- --bar`
    for d in declared:
        if line == d or line.startswith(d + " "):
            return True
    return False


def _is_declared_dep_restore(argv: Sequence[str], root: Path) -> bool:
    try:
        from aider.z.deps import is_safe_declared_dependency_install

        return is_safe_declared_dependency_install(_normalize_cmd_line(argv), root)
    except Exception:
        return False


def classify_command(command: str, *, root: Optional[Path] = None) -> ClassifiedCommand:
    """Classify one shell command into a risk class."""
    root = Path(root) if root else Path(".")
    raw = (command or "").strip()
    argv_list = parse_argv(raw)
    token = make_approval_token(raw)

    if argv_list is None:
        # Unparseable / shell features → treat as unknown (ask)
        return ClassifiedCommand(
            raw=raw,
            argv=tuple(raw.split()),
            risk_class=CommandRiskClass.UNKNOWN,
            approval_token=token,
        )

    argv = tuple(argv_list)
    base = Path(argv[0]).name.lower()

    # Destructive bases
    if base in _DESTRUCTIVE_BASE:
        return ClassifiedCommand(
            raw=raw, argv=argv, risk_class=CommandRiskClass.DESTRUCTIVE, approval_token=token
        )
    if base == "git" and len(argv) >= 2:
        sub = argv[1].lower()
        flags = {a for a in argv[2:] if a.startswith("-")}
        if flags & _GIT_DESTRUCTIVE_FLAGS or sub in {"clean", "reset"} and (
            "--hard" in argv or "-f" in argv or "--force" in argv
        ):
            return ClassifiedCommand(
                raw=raw,
                argv=argv,
                risk_class=CommandRiskClass.DESTRUCTIVE,
                approval_token=token,
            )
        if sub == "push" or (sub == "remote" and any(a in {"add", "remove", "set-url"} for a in argv[2:])):
            return ClassifiedCommand(
                raw=raw,
                argv=argv,
                risk_class=CommandRiskClass.NETWORK_WRITE,
                approval_token=token,
            )
        if sub in _GIT_MUTATING:
            return ClassifiedCommand(
                raw=raw,
                argv=argv,
                risk_class=CommandRiskClass.LOCAL_MUTATION,
                approval_token=token,
            )
        # read-only git subcommands
        allowed = _READ_ONLY_BASE.get("git") or set()
        if sub in allowed:
            # Reject command substitution leftovers already blocked by metachar check
            return ClassifiedCommand(
                raw=raw,
                argv=argv,
                risk_class=CommandRiskClass.READ_ONLY_REPO,
                approval_token=token,
            )

    if _is_declared_dep_restore(argv, root):
        return ClassifiedCommand(
            raw=raw,
            argv=argv,
            risk_class=CommandRiskClass.DECLARED_DEP_RESTORE,
            notice="Auto-approving install of declared project dependencies.",
            approval_token=token,
        )

    if _is_declared_verification(argv, root):
        return ClassifiedCommand(
            raw=raw,
            argv=argv,
            risk_class=CommandRiskClass.DECLARED_VERIFICATION,
            notice=f"Running declared project check: {raw}",
            approval_token=token,
        )

    # find -delete is destructive
    if base == "find" and "-delete" in argv:
        return ClassifiedCommand(
            raw=raw, argv=argv, risk_class=CommandRiskClass.DESTRUCTIVE, approval_token=token
        )

    if base in _READ_ONLY_BASE:
        rule = _READ_ONLY_BASE[base]
        if rule is None:
            return ClassifiedCommand(
                raw=raw,
                argv=argv,
                risk_class=CommandRiskClass.READ_ONLY_REPO,
                approval_token=token,
            )
        # python -m pytest already handled as declared; other -m → unknown
        if base in ("python", "python3") and len(argv) >= 3 and argv[1] == "-m":
            mod = argv[2]
            if mod in ("pytest", "unittest"):
                return ClassifiedCommand(
                    raw=raw,
                    argv=argv,
                    risk_class=CommandRiskClass.DECLARED_VERIFICATION,
                    notice=f"Running declared project check: {raw}",
                    approval_token=token,
                )

    if base in _NETWORK_BASE:
        # npm/pnpm/yarn test already caught as declared; remaining → network/write
        return ClassifiedCommand(
            raw=raw,
            argv=argv,
            risk_class=CommandRiskClass.NETWORK_WRITE,
            approval_token=token,
        )

    # Formatters / local codegen
    if base in {"prettier", "black", "ruff", "isort", "gofmt", "rustfmt", "clang-format"}:
        return ClassifiedCommand(
            raw=raw,
            argv=argv,
            risk_class=CommandRiskClass.LOCAL_MUTATION,
            approval_token=token,
        )

    return ClassifiedCommand(
        raw=raw, argv=argv, risk_class=CommandRiskClass.UNKNOWN, approval_token=token
    )


def make_approval_token(command: str, *, nonce: str = "") -> str:
    """Token bound to the exact pending command instance."""
    payload = f"{command.strip()}\0{nonce}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def policy_auto_approves(risk: CommandRiskClass) -> bool:
    return risk in (
        CommandRiskClass.READ_ONLY_REPO,
        CommandRiskClass.DECLARED_VERIFICATION,
        CommandRiskClass.DECLARED_DEP_RESTORE,
    )


def policy_ask_once_per_class(risk: CommandRiskClass) -> bool:
    return risk is CommandRiskClass.LOCAL_MUTATION


def policy_always_ask(risk: CommandRiskClass) -> bool:
    return risk in (
        CommandRiskClass.NETWORK_WRITE,
        CommandRiskClass.DESTRUCTIVE,
        CommandRiskClass.UNKNOWN,
    )
