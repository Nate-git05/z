"""Artifact hygiene — keep agent internals out of the product repository.

Blocks committing agent histories, caches, uncertainty dumps, skill scratch,
and other internal state that must not enter the product tree.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Sequence, Set


# Paths / globs that should never be committed as product artifacts
_AGENT_ARTIFACT_RE = re.compile(
    r"(?i)("
    r"(^|/)\.z/"
    r"|(^|/)z-uncertainty/"
    r"|(^|/)\.aider"
    r"|(^|/)aider\.chat\.(history|input)\.md$"
    r"|(^|/)\.chat\."
    r"|(^|/)conversation\.json$"
    r"|(^|/)agent[-_]?(history|state|cache|scratch)"
    r"|(^|/)uncertainty[-_]?(dump|export|snapshot)"
    r"|(^|/)\.cursor/(?!rules/)[^/]*$"  # allow .cursor/rules, not caches
    r"|(^|/)__pycache__/"
    r"|(^|/)\.pytest_cache/"
    r"|(^|/)\.mypy_cache/"
    r"|(^|/)node_modules/"
    r"|(^|/)\.next/"
    r"|(^|/)dist/"
    r"|(^|/)coverage/"
    r"|(^|/)\.turbo/"
    r")"
)

# Filenames that look like accidental agent dumps in the repo root
_DUMP_NAME_RE = re.compile(
    r"(?i)^("
    r"untitled.*\.(md|txt|json)"
    r"|scratch\..*"
    r"|tmp_.*\.(py|ts|js|md|json)"
    r"|debug[-_]?output\..*"
    r"|llm[-_]?response\..*"
    r")$"
)


@dataclass
class ArtifactFinding:
    path: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ArtifactReport:
    findings: List[ArtifactFinding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def paths(self) -> List[str]:
        return [f.path for f in self.findings]

    def to_dict(self) -> dict:
        return {"findings": [f.to_dict() for f in self.findings], "clean": self.clean}


def is_agent_artifact(path: str) -> bool:
    rel = (path or "").replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    if _AGENT_ARTIFACT_RE.search(rel):
        return True
    name = Path(rel).name
    if _DUMP_NAME_RE.match(name):
        return True
    return False


def scan_artifacts(
    paths: Sequence[str],
    *,
    root: Path | None = None,
) -> ArtifactReport:
    """Scan edited / staged paths for unintended agent artifacts."""
    report = ArtifactReport()
    seen: Set[str] = set()
    for p in paths or ():
        rel = str(p).replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        rel = rel.lstrip("/")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        if is_agent_artifact(rel):
            report.findings.append(
                ArtifactFinding(
                    path=rel,
                    reason="matches agent/cache/internal artifact pattern",
                )
            )
    if root is not None:
        root = Path(root)
        # Light scan of repo root for dump filenames
        try:
            for child in root.iterdir():
                if child.is_file() and _DUMP_NAME_RE.match(child.name):
                    rel = child.name
                    if rel not in seen:
                        seen.add(rel)
                        report.findings.append(
                            ArtifactFinding(
                                path=rel,
                                reason="looks like an accidental agent dump in repo root",
                            )
                        )
        except OSError:
            pass
    return report


def format_artifact_report(report: ArtifactReport) -> str:
    if report.clean:
        return "Artifact hygiene: clean"
    lines = [
        "Artifact hygiene: unintended agent artifacts detected",
        "Remove these from the product commit:",
    ]
    for f in report.findings:
        lines.append(f"  - {f.path}: {f.reason}")
    return "\n".join(lines)
