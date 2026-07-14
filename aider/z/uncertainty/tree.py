"""Hierarchical tree views over uncertainty nodes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from .schema import Area, UncertaintyNode
from .store import sort_nodes

SortMode = Literal["risk", "file", "session"]


@dataclass
class TreeBranch:
    name: str
    nodes: List[UncertaintyNode] = field(default_factory=list)
    children: Dict[str, "TreeBranch"] = field(default_factory=dict)

    def all_nodes(self) -> List[UncertaintyNode]:
        out = list(self.nodes)
        for child in self.children.values():
            out.extend(child.all_nodes())
        return out


def build_tree(
    nodes: List[UncertaintyNode],
    *,
    mode: SortMode = "risk",
) -> TreeBranch:
    """
    Group by logical area of the codebase for the current task (Frontend/Backend/...),
    not by raw file path. Default ordering still prioritizes risk across the tree.
    """
    root = TreeBranch(name="Uncertainty Tree")

    if mode == "file":
        by_file: Dict[str, List[UncertaintyNode]] = defaultdict(list)
        for n in nodes:
            key = n.files_affected[0] if n.files_affected else "(no file)"
            by_file[key].append(n)
        for fpath, group in sorted(by_file.items()):
            branch = TreeBranch(name=fpath, nodes=sort_nodes(group))
            root.children[fpath] = branch
        return root

    if mode == "session":
        by_task: Dict[str, List[UncertaintyNode]] = defaultdict(list)
        for n in nodes:
            key = n.task_title or n.task_id or n.created_by_session or "session"
            by_task[key].append(n)
        for task, group in sorted(by_task.items(), key=lambda kv: min(x.created_at or "" for x in kv[1])):
            # Chronological within task; still sort nodes by risk inside
            task_branch = TreeBranch(name=task)
            by_area: Dict[str, List[UncertaintyNode]] = defaultdict(list)
            for n in group:
                by_area[n.area.value if isinstance(n.area, Area) else str(n.area)].append(n)
            for area, area_nodes in by_area.items():
                task_branch.children[area] = TreeBranch(name=area, nodes=sort_nodes(area_nodes))
            root.children[task] = task_branch
        return root

    # Default: group by task → area; sort nodes by risk globally when flattening,
    # and within each area branch.
    by_task: Dict[str, Dict[str, List[UncertaintyNode]]] = defaultdict(lambda: defaultdict(list))
    for n in nodes:
        task = n.task_title or n.task_id or "Current task"
        area = n.area.value if isinstance(n.area, Area) else str(n.area)
        by_task[task][area].append(n)

    for task, areas in by_task.items():
        task_branch = TreeBranch(name=task)
        for area, area_nodes in areas.items():
            task_branch.children[area] = TreeBranch(name=area, nodes=sort_nodes(area_nodes))
        root.children[task] = task_branch
    return root


def flatten_for_display(tree: TreeBranch, *, mode: SortMode = "risk") -> List[tuple[str, UncertaintyNode]]:
    """Return (path_label, node) pairs for scannable listing."""
    rows: List[tuple[str, UncertaintyNode]] = []

    def walk(branch: TreeBranch, prefix: str):
        label_prefix = f"{prefix} / {branch.name}" if prefix else branch.name
        for n in branch.nodes:
            rows.append((label_prefix, n))
        for child in branch.children.values():
            walk(child, label_prefix if branch.name != "Uncertainty Tree" else "")

    walk(tree, "")
    if mode == "risk":
        # Re-sort globally by risk regardless of grouping for the default attention order
        node_order = {id(n): i for i, n in enumerate(sort_nodes([r[1] for r in rows]))}
        rows.sort(key=lambda r: node_order.get(id(r[1]), 9999))
    return rows
