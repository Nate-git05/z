"""In-memory + on-disk uncertainty node store, with optional remote sync."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .schema import NodeStatus, Tier, UncertaintyNode, TIER_RANK


class UncertaintyStore:
    """Session-local store that can persist to ~/.z/uncertainty and sync to z_server."""

    def __init__(
        self,
        *,
        root: Optional[Path] = None,
        repo_key: Optional[str] = None,
        created_by_session: Optional[str] = None,
        created_by_user: Optional[str] = None,
        remote_sync: Optional[Callable[[UncertaintyNode], None]] = None,
    ):
        self.nodes: dict[str, UncertaintyNode] = {}
        self.root = Path(root) if root else None
        self.repo_key = repo_key or (str(root) if root else "default")
        self.created_by_session = created_by_session
        self.created_by_user = created_by_user
        self.remote_sync = remote_sync
        self._local_path = self._default_local_path()
        self.load_local()

    def _default_local_path(self) -> Path:
        base = Path(os.environ.get("Z_HOME", Path.home() / ".z"))
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.repo_key)[:80]
        return base / "uncertainty" / f"{safe or 'default'}.json"

    def add(self, node: UncertaintyNode, *, sync: bool = True) -> UncertaintyNode:
        if not node.created_by_session and self.created_by_session:
            node.created_by_session = self.created_by_session
        if not node.created_by_user and self.created_by_user:
            node.created_by_user = self.created_by_user
        self.nodes[node.id] = node
        self.save_local()
        if sync and self.remote_sync:
            try:
                self.remote_sync(node)
            except Exception:
                pass
        return node

    def add_many(self, nodes: Iterable[UncertaintyNode], *, sync: bool = True) -> List[UncertaintyNode]:
        out = []
        for n in nodes:
            out.append(self.add(n, sync=sync))
        return out

    def get(self, node_id: str) -> Optional[UncertaintyNode]:
        return self.nodes.get(node_id)

    def list(
        self,
        *,
        include_resolved: bool = False,
        task_id: Optional[str] = None,
    ) -> List[UncertaintyNode]:
        nodes = list(self.nodes.values())
        if task_id:
            nodes = [n for n in nodes if n.task_id == task_id]
        if not include_resolved:
            nodes = [
                n
                for n in nodes
                if n.status not in (NodeStatus.RESOLVED, NodeStatus.IGNORED)
            ]
        return sort_nodes(nodes)

    def update_status(self, node_id: str, status: NodeStatus) -> Optional[UncertaintyNode]:
        node = self.nodes.get(node_id)
        if not node:
            return None
        node.status = status
        if status in (NodeStatus.RESOLVED, NodeStatus.IGNORED):
            node.resolved_at = datetime.now(timezone.utc).isoformat()
        self.save_local()
        if self.remote_sync:
            try:
                self.remote_sync(node)
            except Exception:
                pass
        return node

    def save_local(self) -> None:
        path = self._local_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "repo_key": self.repo_key,
                "nodes": [n.to_dict() for n in self.nodes.values()],
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def load_local(self) -> None:
        path = self._local_path
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for raw in data.get("nodes") or []:
                try:
                    node = UncertaintyNode.from_dict(raw)
                    self.nodes[node.id] = node
                except (KeyError, ValueError, TypeError):
                    continue
        except (OSError, json.JSONDecodeError):
            pass

    def merge_remote(self, remote_nodes: Iterable[dict]) -> int:
        added = 0
        for raw in remote_nodes:
            try:
                node = UncertaintyNode.from_dict(raw)
            except (KeyError, ValueError, TypeError):
                continue
            existing = self.nodes.get(node.id)
            if not existing:
                self.nodes[node.id] = node
                added += 1
            else:
                # Prefer newer resolved_at / status from remote workspace
                self.nodes[node.id] = node
        if added:
            self.save_local()
        return added


def sort_nodes(nodes: List[UncertaintyNode]) -> List[UncertaintyNode]:
    """Default: risk tier first (High→Low), then confidence (Low confidence first = more uncertain)."""
    conf_rank = {Tier.LOW: 0, Tier.MEDIUM: 1, Tier.HIGH: 2}  # low confidence bubbles up after risk

    def key(n: UncertaintyNode):
        return (
            TIER_RANK.get(n.risk_tier, 9),
            conf_rank.get(n.confidence_tier, 9),
            n.created_at or "",
        )

    return sorted(nodes, key=key)
