"""Dependency manifest parsing + fabrication / import-shadow detection.

Catches the failure mode where the agent writes a local top-level package
(e.g. freezegun/__init__.py) to fake a real third-party dependency after an
install/import failure — silently shadowing the real library for the suite.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

# Common stdlib / always-local top-level names — never treat as fabrication
_LOCAL_ALLOWLIST = {
    "src",
    "lib",
    "app",
    "apps",
    "pkg",
    "pkgs",
    "tests",
    "test",
    "docs",
    "doc",
    "scripts",
    "script",
    "tools",
    "examples",
    "example",
    "vendor",
    "vendored",
    "third_party",
    "thirdparty",
    "build",
    "dist",
    "assets",
    "static",
    "templates",
    "migrations",
    "alembic",
    "config",
    "configs",
    "data",
    "bin",
    "cmd",
    "internal",
    "pkg",
    "aider",
    "z_server",
}

_MODULE_NOT_FOUND_RE = re.compile(
    r"(?i)(?:ModuleNotFoundError|ImportError):\s*"
    r"(?:No module named ['\"]([^'\"]+)['\"]|cannot import name ['\"]?(\w+))"
    r"|No module named ['\"]([^'\"]+)['\"]"
)

_PIP_INSTALL_TARGET_RE = re.compile(
    r"(?i)\bpip(?:3)?\s+install\b([^\n]*)"
)

_REQ_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9_.+\-]*)"
)

# PyPI name → import name heuristics (freezegun→freezegun, Pillow→PIL, …)
_KNOWN_IMPORT_ALIASES = {
    "pillow": "pil",
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
    "beautifulsoup4": "bs4",
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
    "msgpack-python": "msgpack",
}


def normalize_dep_name(name: str) -> str:
    """Normalize a package/import name for comparison."""
    n = (name or "").strip().lower()
    n = n.replace("-", "_")
    # Take top-level only: freezegun.api → freezegun
    if "." in n:
        n = n.split(".", 1)[0]
    return n


def extract_missing_modules(text: str) -> Set[str]:
    """Parse ModuleNotFoundError / No module named … from command or test output."""
    found: Set[str] = set()
    if not text:
        return found
    for m in _MODULE_NOT_FOUND_RE.finditer(text):
        for g in m.groups():
            if g:
                found.add(normalize_dep_name(g))
    return found


def extract_pip_install_targets(text: str) -> Set[str]:
    """Best-effort package names from pip install command lines in session log."""
    found: Set[str] = set()
    if not text:
        return found
    for m in _PIP_INSTALL_TARGET_RE.finditer(text):
        rest = m.group(1) or ""
        for tok in rest.split():
            if tok.startswith("-"):
                continue
            if tok.endswith(".txt") or "/" in tok or tok.startswith("."):
                continue
            # strip extras / versions: freezegun==1.2 / package[extra]
            base = re.split(r"[\[<=>!~]", tok, maxsplit=1)[0]
            base = normalize_dep_name(base)
            if base and base not in {"pip", "setuptools", "wheel"}:
                found.add(base)
    return found


def _names_from_requirements_text(text: str) -> Set[str]:
    names: Set[str] = set()
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-"):
            continue
        m = _REQ_LINE_RE.match(s)
        if not m:
            continue
        raw = m.group(1)
        # Skip URLs / editable
        if "://" in raw or raw.startswith("git+"):
            continue
        n = normalize_dep_name(raw)
        if n:
            names.add(n)
            alias = _KNOWN_IMPORT_ALIASES.get(n.replace("_", "-")) or _KNOWN_IMPORT_ALIASES.get(n)
            if alias:
                names.add(normalize_dep_name(alias))
            # Also register hyphen form as underscore already done
            names.add(n.replace("_", "-").replace("-", "_"))  # noop normalize
    return names


def _names_from_pyproject(text: str) -> Set[str]:
    names: Set[str] = set()
    # Lightweight parse — avoid requiring tomllib for older paths
    # dependencies = [ "freezegun>=1", ... ] or freezegun = "^1"
    for m in re.finditer(
        r"""['\"]([A-Za-z0-9][A-Za-z0-9_.+\-]*)(?:\[[^\]]*\])?(?:[<=>!~][^'\"]*)?['\"]""",
        text or "",
    ):
        n = normalize_dep_name(m.group(1))
        if n and n not in {"python", "poetry", "hatchling", "setuptools", "wheel"}:
            names.add(n)
    # PEP 621 / poetry style name = "version"
    for m in re.finditer(
        r"(?m)^\s*([A-Za-z0-9][A-Za-z0-9_.+\-]*)\s*=\s*[\"'][^\"']+[\"']",
        text or "",
    ):
        n = normalize_dep_name(m.group(1))
        if n and n not in {
            "name",
            "version",
            "description",
            "readme",
            "requires_python",
            "license",
            "authors",
            "python",
        }:
            names.add(n)
    return names


def _names_from_package_json(text: str) -> Set[str]:
    names: Set[str] = set()
    # "freezegun": "^1.0" style inside dependencies blocks — take quoted keys
    for m in re.finditer(r'"(@?[^"]+)"\s*:\s*"([^"]*)"', text or ""):
        key = m.group(1)
        if key in {
            "name",
            "version",
            "description",
            "main",
            "license",
            "scripts",
            "type",
            "private",
            "author",
        }:
            continue
        if key.startswith("//"):
            continue
        # npm scoped @scope/pkg → pkg for top-level check is wrong; keep full and last
        n = normalize_dep_name(key.split("/")[-1].lstrip("@"))
        if n:
            names.add(n)
    return names


def collect_declared_dependencies(root: Path) -> Set[str]:
    """Union of dependency names from common manifests under root."""
    root = Path(root)
    names: Set[str] = set()
    candidates = [
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "dev-requirements.txt",
        "test-requirements.txt",
        "requirements_dev.txt",
        "requirements_test.txt",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "Pipfile",
        "package.json",
    ]
    # Also scan common subdirs
    globs = [
        "requirements*.txt",
        "**/requirements*.txt",
        "dev/requirements*.txt",
        "requirements/*.txt",
    ]
    paths: List[Path] = []
    for name in candidates:
        p = root / name
        if p.is_file():
            paths.append(p)
    for pattern in globs:
        try:
            for p in root.glob(pattern):
                if p.is_file() and p not in paths:
                    paths.append(p)
        except OSError:
            continue

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lower = path.name.lower()
        if lower.endswith(".txt") or lower == "pipfile":
            names |= _names_from_requirements_text(text)
        elif lower == "pyproject.toml" or lower == "setup.cfg":
            names |= _names_from_pyproject(text)
        elif lower == "setup.py":
            names |= _names_from_pyproject(text)
        elif lower == "package.json":
            names |= _names_from_package_json(text)

    # Normalize aliases
    extra: Set[str] = set()
    for n in list(names):
        hyphen = n.replace("_", "-")
        under = n.replace("-", "_")
        extra.add(under)
        alias = _KNOWN_IMPORT_ALIASES.get(hyphen) or _KNOWN_IMPORT_ALIASES.get(under)
        if alias:
            extra.add(normalize_dep_name(alias))
    names |= extra
    return {n for n in names if n and len(n) > 1}


def top_level_module_name(rel_path: str) -> Optional[str]:
    """
    If rel_path introduces a top-level Python package/module, return its name.

    Examples:
      freezegun/__init__.py → freezegun
      freezegun.py → freezegun
      src/freezegun/__init__.py → None (not top-level)
      tests/test_x.py → None
    """
    p = Path(str(rel_path).replace("\\", "/"))
    parts = p.parts
    if not parts:
        return None
    first = parts[0]
    if first.startswith(".") or first in _LOCAL_ALLOWLIST:
        return None
    # freezegun/__init__.py or freezegun/api.py
    if len(parts) >= 2:
        if first.endswith(".py"):
            return None
        # Only treat as package root if it's a directory-style path
        name = normalize_dep_name(first)
        if name and name.isidentifier() or (name and name.replace("_", "").isalnum()):
            return name
        return name or None
    # single file freezegun.py at root
    if len(parts) == 1 and first.endswith(".py"):
        stem = Path(first).stem
        if stem.startswith("test_") or stem.endswith("_test"):
            return None
        if stem in _LOCAL_ALLOWLIST:
            return None
        return normalize_dep_name(stem)
    return None


def path_already_in_repo(root: Path, rel_path: str) -> bool:
    """True if the top-level package/module already existed before this write."""
    root = Path(root)
    name = top_level_module_name(rel_path)
    if not name:
        return True
    # Existing package dir or module file
    if (root / name).is_dir():
        return True
    if (root / f"{name}.py").is_file():
        return True
    return False


def is_dependency_fabrication(
    rel_path: str,
    *,
    root: Path,
    declared: Optional[Set[str]] = None,
    missing_modules: Optional[Set[str]] = None,
    preexisting_top_level: Optional[Set[str]] = None,
) -> Optional[str]:
    """
    Return a reason string if creating rel_path looks like dependency fabrication.

    Mechanical signal:
      - new top-level module/package name
      - AND (name in declared deps OR name was ModuleNotFound earlier this session)
    """
    name = top_level_module_name(rel_path)
    if not name or name in _LOCAL_ALLOWLIST:
        return None

    root = Path(root)
    # Already part of the project tree (not a new shadow)
    if preexisting_top_level is not None:
        if name in preexisting_top_level:
            return None
    else:
        # If the package already exists with real content beyond this file, skip
        pkg = root / name
        if pkg.is_dir():
            try:
                others = [
                    p
                    for p in pkg.rglob("*")
                    if p.is_file() and p.resolve() != (root / rel_path).resolve()
                ]
            except OSError:
                others = []
            if others:
                return None
        elif (root / f"{name}.py").is_file():
            # Overwriting existing project module — not fabrication of a new shadow
            try:
                target = (root / rel_path).resolve()
                existing = (root / f"{name}.py").resolve()
                if target == existing:
                    return None
            except OSError:
                pass

    declared = declared if declared is not None else collect_declared_dependencies(root)
    missing = {normalize_dep_name(m) for m in (missing_modules or set())}

    in_declared = name in declared or name.replace("_", "-") in {
        d.replace("_", "-") for d in declared
    }
    in_missing = name in missing

    if not in_declared and not in_missing:
        return None

    reasons = []
    if in_missing:
        reasons.append(f"matches a failed import/install ({name})")
    if in_declared:
        reasons.append(f"matches a declared project dependency ({name})")
    return (
        f"Creating top-level '{name}' would shadow a third-party package "
        f"({' and '.join(reasons)}). Install the real dependency instead of "
        f"fabricating a local stand-in."
    )


def scan_paths_for_fabrication(
    paths: Sequence[str],
    *,
    root: Path,
    missing_modules: Optional[Iterable[str]] = None,
    declared: Optional[Set[str]] = None,
) -> List[dict]:
    """Scan relative paths; return list of {path, package, reason} hits."""
    root = Path(root)
    declared = declared if declared is not None else collect_declared_dependencies(root)
    missing = {normalize_dep_name(m) for m in (missing_modules or [])}
    # Preexisting top-level packages (dirs/modules present that are NOT only
    # introduced by the paths under scan)
    preexisting: Set[str] = set()
    try:
        for child in root.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir() and (child / "__init__.py").is_file():
                preexisting.add(normalize_dep_name(child.name))
            elif child.is_file() and child.suffix == ".py":
                preexisting.add(normalize_dep_name(child.stem))
    except OSError:
        pass

    # Names introduced by this path set
    introducing = set()
    for p in paths:
        n = top_level_module_name(p)
        if n:
            introducing.add(n)

    hits: List[dict] = []
    seen: Set[str] = set()
    for p in paths:
        n = top_level_module_name(p)
        if not n or n in seen:
            continue
        # Was this name already a project package before the new files?
        # If preexisting and the path is under it, only flag if it was ALSO
        # just introduced (i.e. preexisting counted the new dir we just made).
        # Safer: flag when name matches declared/missing AND path is new top-level.
        reason = is_dependency_fabrication(
            p,
            root=root,
            declared=declared,
            missing_modules=missing,
            preexisting_top_level=preexisting - introducing,
        )
        if reason:
            seen.add(n)
            hits.append({"path": p, "package": n, "reason": reason})
    return hits


# Prompt text shared with coder system reminders
DEPENDENCY_FABRICATION_RULE = """\
CRITICAL — Dependency / import failures:
If a required third-party package cannot be imported or installed, do NOT create a local
file or package with the same import name to replace it (for example freezegun/__init__.py
that only "satisfies imports"). That shadows the real library for the whole test suite.
Instead: install the real dependency from the project's requirements / PyPI, or STOP,
report the exact install/import failure verbatim, and wait for human direction.
Allowed recovery: pip install of a declared dependency; fixing the code/tests under review.
Forbidden without explicit human approval: fabricating shadow packages, editing unrelated
conftest/CI to hide import errors, skipping tests to go green.
"""
