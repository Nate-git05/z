"""In-memory + on-disk uncertainty node store, with optional remote sync."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .schema import NodeStatus, Tier, UncertaintyNode, TIER_RANK

logger = logging.getLogger(__name__)

# Phase 6 — live subscribe: process-wide listeners for store mutations.
# Signature: (node, event) where event is "upsert" | "update"
StoreListener = Callable[[UncertaintyNode, str], None]
_STORE_LISTENERS: List[StoreListener] = []


def add_store_listener(callback: StoreListener) -> None:
    if callback not in _STORE_LISTENERS:
        _STORE_LISTENERS.append(callback)


def remove_store_listener(callback: StoreListener) -> None:
    try:
        _STORE_LISTENERS.remove(callback)
    except ValueError:
        pass


def _emit_store_event(node: UncertaintyNode, event: str) -> None:
    for cb in list(_STORE_LISTENERS):
        try:
            cb(node, event)
        except Exception:
            logger.debug("uncertainty store listener failed", exc_info=True)


def local_store_filename(repo_key: str) -> str:
    """
    Stable, collision-resistant filename for a repo_key.

    Keep a short human-readable prefix, then a content hash so two long paths
    that share an 80-char sanitized prefix never collapse to the same file.
    """
    key = repo_key or "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    prefix = (safe[:40] if safe else "default").rstrip("_") or "default"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}.json"


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
        return base / "uncertainty" / local_store_filename(self.repo_key)

    def add(self, node: UncertaintyNode, *, sync: bool = True) -> UncertaintyNode:
        if not node.created_by_session and self.created_by_session:
            node.created_by_session = self.created_by_session
        if not node.created_by_user and self.created_by_user:
            node.created_by_user = self.created_by_user
        # P1.2 — every node gets a resolution contract at creation
        try:
            from .resolution import attach_contract_to_node

            if not (node.signals or {}).get("resolution_contract"):
                attach_contract_to_node(node)
        except Exception:
            # Contract attach is integrity-adjacent but must not drop the node
            logger.debug("resolution contract attach failed for %s", node.id)
        is_new = node.id not in self.nodes
        self.nodes[node.id] = node
        self.save_local()
        if is_new:
            try:
                from .outcomes import record_node_created

                record_node_created(node, repo_key=self.repo_key)
            except Exception:
                pass
        try:
            _emit_store_event(node, "upsert" if is_new else "update")
        except Exception:
            pass
        if sync and self.remote_sync:
            self._enqueue_remote(node)
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
        prev = node.status
        node.status = status
        if status in (NodeStatus.RESOLVED, NodeStatus.IGNORED):
            node.resolved_at = datetime.now(timezone.utc).isoformat()
        self.save_local()
        if status != prev and status in (NodeStatus.RESOLVED, NodeStatus.IGNORED):
            try:
                from .outcomes import record_outcome

                disposition = (
                    "ignored" if status == NodeStatus.IGNORED else "resolved"
                )
                record_outcome(
                    node.type,
                    disposition,
                    repo_key=self.repo_key,
                    node_id=node.id,
                )
            except Exception:
                pass
        if self.remote_sync:
            self._enqueue_remote(node)
        return node

    def _enqueue_remote(self, node: UncertaintyNode) -> None:
        """Local-write-then-async-sync — never block the agent loop on network."""
        sync_fn = self.remote_sync
        if not sync_fn:
            return
        try:
            from .sync_outbox import enqueue_node_sync

            version = (
                getattr(node, "resolved_at", None)
                or getattr(node, "updated_at", None)
                or getattr(node, "created_at", None)
                or node.status.value
                or "v"
            )
            # Capture current node snapshot for the worker
            node_id = node.id

            def _payload() -> bool:
                current = self.nodes.get(node_id, node)
                result = sync_fn(current)
                return bool(result) if result is not None else True

            enqueue_node_sync(node_id, str(version), _payload)
        except Exception:
            # Outbox unavailable — drop the sync rather than block the loop
            logger.debug("uncertainty async enqueue failed; skipping remote sync")

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
            stored_key = data.get("repo_key")
            if stored_key is not None and stored_key != self.repo_key:
                # Collision or reused filename — never silently mix node graphs
                logger.warning(
                    "Uncertainty store repo_key mismatch at %s "
                    "(stored=%r current=%r); starting fresh",
                    path,
                    stored_key,
                    self.repo_key,
                )
                self.nodes = {}
                return
            for raw in data.get("nodes") or []:
                try:
                    node = UncertaintyNode.from_dict(raw)
                    self.nodes[node.id] = node
                except (KeyError, ValueError, TypeError):
                    continue
        except (OSError, json.JSONDecodeError):
            pass

    def merge_remote(self, remote_nodes: Iterable[dict]) -> int:
        """
        Merge workspace history. Temporary blockers are excluded (P1.2);
        persistent risks are labeled as carried-over context.
        """
        added = 0
        for raw in remote_nodes:
            try:
                node = UncertaintyNode.from_dict(raw)
            except (KeyError, ValueError, TypeError):
                continue
            signals = dict(node.signals or {})
            lifecycle = signals.get("lifecycle") or ""
            expires = bool(signals.get("expires_after_task"))
            # Temporary execution blockers must not leak across sessions
            if lifecycle == "temporary_blocker" or (
                expires and signals.get("shell_approval_block")
            ):
                continue
            node.signals = signals
            node.signals["carried_over"] = True
            node.signals["lifecycle"] = signals.get("lifecycle") or "persistent_risk"
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
