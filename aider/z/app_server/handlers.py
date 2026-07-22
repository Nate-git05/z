"""Request handlers for z-app-server IPC v0 / Phase 4 turns."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

from aider.z.app_server.protocol import PROTOCOL_VERSION, SERVER_NAME

NotifyFn = Callable[[str, dict], None]


class AppServerSession:
    """Per-connection session state."""

    def __init__(self, notify: Optional[NotifyFn] = None) -> None:
        self.initialized = False
        self.client_info: dict[str, Any] = {}
        self.workspace_root: Optional[str] = None
        self._notify: NotifyFn = notify or (lambda _m, _p: None)
        self._turns = None  # lazy TurnManager
        self._uncertainty_subscribed = False
        self._uncertainty_listener = None

    def _turn_manager(self):
        if self._turns is None:
            from aider.z.app_server.turn_runner import TurnManager

            self._turns = TurnManager(
                workspace_root=self.workspace_root,
                notify=self._notify,
            )
        return self._turns

    def dispose(self) -> None:
        """Drop live uncertainty subscription when the WS session ends."""
        self._uncertainty_unsubscribe()

    def handle(self, method: str, params: Optional[dict]) -> Any:
        params = params or {}
        if method == "initialize":
            return self._initialize(params)
        # Health is allowed pre-initialize so spawn/attach can probe quickly.
        if method == "server/health":
            return self._server_health(params)
        if not self.initialized:
            raise HandlerError(-32000, "Not initialized")
        if method == "workspace/open":
            return self._workspace_open(params)
        if method == "workspace/info":
            return self._workspace_info(params)
        if method == "uncertainty/list":
            return self._uncertainty_list(params)
        if method == "uncertainty/subscribe":
            return self._uncertainty_subscribe(params)
        if method == "uncertainty/unsubscribe":
            return self._uncertainty_unsubscribe(params)
        if method == "skills/list":
            return self._skills_list(params)
        if method == "skills/get":
            return self._skills_get(params)
        if method == "skills/create":
            return self._skills_create(params)
        if method == "commit_blocks/list":
            return self._commit_blocks_list(params)
        if method == "commit_blocks/override":
            return self._commit_blocks_override(params)
        if method == "commit_blocks/resolve":
            return self._commit_blocks_resolve(params)
        if method == "mcp/list":
            return self._mcp_list(params)
        if method == "mcp/catalog":
            return self._mcp_catalog(params)
        if method == "mcp/connect":
            return self._mcp_connect(params)
        if method == "mcp/disconnect":
            return self._mcp_disconnect(params)
        if method == "mcp/test":
            return self._mcp_test(params)
        if method == "mcp/confirmFirstUse":
            return self._mcp_confirm_first_use(params)
        if method == "mcp/firstUseStatus":
            return self._mcp_first_use_status(params)
        if method == "mcp/sync":
            return self._mcp_sync(params)
        if method == "mcp/tools":
            return self._mcp_tools(params)
        if method == "mcp/oauthStart":
            return self._mcp_oauth_start(params)
        if method == "auth/status":
            return self._auth_status(params)
        if method == "auth/loginStart":
            return self._auth_login_start(params)
        if method == "auth/loginStatus":
            return self._auth_login_status(params)
        if method == "auth/loginCancel":
            return self._auth_login_cancel(params)
        if method == "auth/logout":
            return self._auth_logout(params)
        if method == "usage/summary":
            return self._usage_summary(params)
        if method == "turn/start":
            return self._turn_start(params)
        if method == "turn/respond":
            return self._turn_respond(params)
        if method == "turn/cancel":
            return self._turn_cancel(params)
        raise HandlerError(-32601, f"Method not found: {method}")

    def _initialize(self, params: dict) -> dict:
        self.client_info = dict(params.get("clientInfo") or {})
        root = params.get("workspaceRoot")
        if root:
            self.workspace_root = str(root)
            try:
                self._turn_manager().set_workspace(self.workspace_root)
            except Exception:
                pass
        self.initialized = True
        z_home = os.environ.get("Z_HOME") or str(Path.home() / ".z")
        return {
            "serverInfo": {"name": SERVER_NAME, "version": PROTOCOL_VERSION},
            "zHome": z_home,
            "capabilities": [
                "uncertainty",
                "uncertainty_subscribe",
                "skills",
                "commit_blocks",
                "mcp",
                "mcp_manage",
                "mcp_runtime",
                "mcp_oauth",
                "usage",
                "turns",
                "auth",
                "workspace",
            ],
            "workspaceRoot": self.workspace_root,
        }

    def _server_health(self, params: dict) -> dict:
        del params
        return {
            "ok": True,
            "initialized": self.initialized,
            "serverInfo": {"name": SERVER_NAME, "version": PROTOCOL_VERSION},
            "workspaceRoot": self.workspace_root,
            "pid": os.getpid(),
        }

    def _workspace_open(self, params: dict) -> dict:
        root = (params.get("root") or "").strip()
        if not root:
            raise HandlerError(-32602, "workspace/open requires root")
        path = Path(root).expanduser().resolve()
        if not path.is_dir():
            raise HandlerError(-32004, f"Not a directory: {path}")
        self.workspace_root = str(path)
        try:
            self._turn_manager().set_workspace(self.workspace_root)
        except Exception:
            pass
        return {"ok": True, "root": self.workspace_root}

    def _workspace_info(self, params: dict) -> dict:
        del params
        root = self.workspace_root
        return {
            "root": root,
            "open": bool(root),
            "name": Path(root).name if root else None,
        }

    @staticmethod
    def _node_payload(node) -> dict:
        """Serialize node + flatten ResolutionContract for the chain UI."""
        d = node.to_dict()
        signals = dict(getattr(node, "signals", None) or {})
        contract = signals.get("resolution_contract")
        if not isinstance(contract, dict):
            try:
                from aider.z.uncertainty.resolution import contract_for_node

                contract = contract_for_node(node).to_dict()
            except Exception:
                contract = None
        d["resolution_contract"] = contract
        if contract:
            d["expires_after_task"] = bool(contract.get("expires_after_task"))
        return d

    def _uncertainty_list(self, params: dict) -> dict:
        sort = (params.get("sort") or "risk").strip().lower()
        include_resolved = bool(params.get("includeResolved") or params.get("include_resolved"))
        status_filter = (params.get("status") or "").strip() or None
        nodes: list[dict] = []
        try:
            from aider.z.uncertainty.store import UncertaintyStore
            from aider.z.uncertainty.schema import NodeStatus, TIER_RANK

            store = UncertaintyStore(root=self.workspace_root)
            items = list(store.nodes.values())
            if not include_resolved:
                items = [
                    n
                    for n in items
                    if n.status
                    not in (NodeStatus.RESOLVED, NodeStatus.IGNORED)
                ]
            if status_filter:
                items = [
                    n
                    for n in items
                    if (n.status.value if hasattr(n.status, "value") else str(n.status))
                    == status_filter
                ]
            if sort == "age":
                items.sort(key=lambda n: n.created_at or "", reverse=True)
            elif sort == "type":
                items.sort(key=lambda n: (n.type.value, n.title or ""))
            elif sort == "status":
                items.sort(
                    key=lambda n: (
                        n.status.value if hasattr(n.status, "value") else str(n.status),
                        TIER_RANK.get(n.risk_tier, 99),
                    )
                )
            else:
                items.sort(
                    key=lambda n: (
                        TIER_RANK.get(n.risk_tier, 99),
                        n.created_at or "",
                    )
                )
            nodes = [self._node_payload(n) for n in items]
        except Exception as err:
            raise HandlerError(-32010, f"uncertainty/list failed: {err}") from err
        return {
            "nodes": nodes,
            "sort": sort,
            "subscribed": self._uncertainty_subscribed,
            "includeResolved": include_resolved,
        }

    def _uncertainty_subscribe(self, params: dict) -> dict:
        del params
        if self._uncertainty_subscribed and self._uncertainty_listener is not None:
            return {"ok": True, "subscribed": True}
        from aider.z.uncertainty.store import add_store_listener

        def _on_mutate(node, event: str) -> None:
            # Only forward nodes for this workspace when repo_key matches.
            try:
                payload = self._node_payload(node)
            except Exception:
                payload = node.to_dict() if hasattr(node, "to_dict") else {}
            self._notify(
                "uncertainty/upsert",
                {
                    "node": payload,
                    "event": event,
                    "workspaceRoot": self.workspace_root,
                },
            )
            self._notify(
                "uncertainty/changed",
                {"reason": f"store_{event}", "nodeId": getattr(node, "id", None)},
            )

        self._uncertainty_listener = _on_mutate
        add_store_listener(_on_mutate)
        self._uncertainty_subscribed = True
        return {"ok": True, "subscribed": True}

    def _uncertainty_unsubscribe(self, params: Optional[dict] = None) -> dict:
        del params
        if self._uncertainty_listener is not None:
            from aider.z.uncertainty.store import remove_store_listener

            remove_store_listener(self._uncertainty_listener)
            self._uncertainty_listener = None
        self._uncertainty_subscribed = False
        return {"ok": True, "subscribed": False}

    def _skills_list(self, params: dict) -> dict:
        kind = (params.get("kind") or "").strip() or None
        quality = (params.get("quality_state") or "").strip() or None
        query = (params.get("query") or "").strip().lower() or None
        needs_review = params.get("needs_review")
        if needs_review is None:
            needs_review = params.get("needsReview")
        skills: list[dict] = []
        try:
            from aider.z.skills.store import LocalSkillStore

            for s in LocalSkillStore().list_skills():
                if kind and (getattr(s, "kind", None) or "") != kind:
                    continue
                if quality and (getattr(s, "quality_state", None) or "") != quality:
                    continue
                if needs_review is not None:
                    want = bool(needs_review)
                    if bool(getattr(s, "needs_review", False)) != want:
                        continue
                blob = " ".join(
                    [
                        getattr(s, "title", "") or "",
                        getattr(s, "description", "") or "",
                        " ".join(getattr(s, "triggers", None) or []),
                        getattr(s, "capability", "") or "",
                        getattr(s, "symptom_description", "") or "",
                    ]
                ).lower()
                if query and query not in blob:
                    continue
                skills.append(self._skill_summary(s))
        except Exception as err:
            raise HandlerError(-32011, f"skills/list failed: {err}") from err
        return {"skills": skills}

    @staticmethod
    def _skill_summary(s) -> dict:
        return {
            "id": getattr(s, "id", None),
            "title": getattr(s, "title", None),
            "kind": getattr(s, "kind", None),
            "description": getattr(s, "description", None),
            "triggers": list(getattr(s, "triggers", None) or []),
            "capability": getattr(s, "capability", None),
            "quality_state": getattr(s, "quality_state", None),
            "needs_review": bool(getattr(s, "needs_review", False)),
            "source": getattr(s, "source", None),
            "updated_at": getattr(s, "updated_at", None),
            "symptom_description": getattr(s, "symptom_description", None) or "",
            "root_cause_category": getattr(s, "root_cause_category", None) or "",
        }

    def _skills_get(self, params: dict) -> dict:
        skill_id = (params.get("id") or params.get("skillId") or "").strip()
        if not skill_id:
            raise HandlerError(-32602, "skills/get requires id")
        try:
            from aider.z.skills.store import LocalSkillStore

            store = LocalSkillStore()
            for s in store.list_skills():
                if getattr(s, "id", None) == skill_id:
                    return {"skill": s.to_dict()}
                # Allow short id prefix
                if (getattr(s, "id", None) or "").startswith(skill_id) and len(skill_id) >= 8:
                    return {"skill": s.to_dict()}
            raise HandlerError(-32025, f"Skill not found: {skill_id}")
        except HandlerError:
            raise
        except Exception as err:
            raise HandlerError(-32011, f"skills/get failed: {err}") from err

    def _skills_create(self, params: dict) -> dict:
        draft = params.get("skill") or {}
        if not isinstance(draft, dict):
            raise HandlerError(-32602, "skills/create requires skill object")
        force = bool(params.get("force"))
        merge = bool(params.get("merge"))
        try:
            from aider.z.skills.schema import Skill, VALID_SKILL_KINDS
            from aider.z.skills.store import LocalSkillStore
            from aider.z.skills.near_dup import (
                find_near_dup,
                merge_into_existing,
                near_dup_enabled,
            )

            title = (draft.get("title") or draft.get("name") or "").strip()
            if not title:
                raise HandlerError(-32602, "skill.title is required")
            kind = (draft.get("kind") or "playbook").strip() or "playbook"
            if kind not in VALID_SKILL_KINDS:
                raise HandlerError(
                    -32602,
                    f"skill.kind must be one of {sorted(VALID_SKILL_KINDS)}",
                )
            triggers = draft.get("triggers") or []
            if isinstance(triggers, str):
                triggers = [t.strip() for t in triggers.split(",") if t.strip()]
            skill = Skill(
                title=title,
                description=(draft.get("description") or "").strip(),
                content=(draft.get("content") or draft.get("body") or "").strip(),
                kind=kind,
                triggers=list(triggers),
                capability=(draft.get("capability") or "").strip(),
                symptom_description=(draft.get("symptom_description") or "").strip(),
                root_cause_category=(draft.get("root_cause_category") or "").strip(),
                source="manual",
                quality_state="draft",
                needs_review=True,
            )
            store = LocalSkillStore()
            near = None
            if near_dup_enabled():
                existing = list(store.list_skills())
                hit = find_near_dup(skill, existing)
                if hit is not None:
                    near = {
                        "id": hit.skill.id,
                        "title": hit.skill.title,
                        "kind": hit.skill.kind,
                        "quality_state": hit.skill.quality_state,
                        "score": hit.score,
                        "reason": hit.reason,
                    }
                    if merge:
                        merged = merge_into_existing(
                            hit.skill,
                            skill,
                            grounding_note=f"Manual author merge ({hit.reason}).",
                        )
                        merged.needs_review = True
                        merged.quality_state = "draft"
                        store.save(merged)
                        return {
                            "created": True,
                            "merged": True,
                            "skill": merged.to_dict(),
                            "near_dup": near,
                        }
                    if not force:
                        return {
                            "created": False,
                            "merged": False,
                            "skill": None,
                            "draft": skill.to_dict(),
                            "near_dup": near,
                            "message": (
                                "Near-duplicate skill found. Pass merge=true to "
                                "update it, or force=true to create anyway."
                            ),
                        }
            store.save(skill)
            return {
                "created": True,
                "merged": False,
                "skill": skill.to_dict(),
                "near_dup": near,
            }
        except HandlerError:
            raise
        except Exception as err:
            raise HandlerError(-32012, f"skills/create failed: {err}") from err

    def _commit_blocks_list(self, params: dict) -> dict:
        del params
        try:
            from aider.z.uncertainty.commit_block_ledger import list_blocks

            blocks = list_blocks(repo_key=self.workspace_root)
            blocked = [b for b in blocks if (b.get("state") or "blocked") == "blocked"]
            return {
                "blocks": blocks,
                "blockedCount": len(blocked),
                "canCommit": len(blocked) == 0,
            }
        except Exception as err:
            raise HandlerError(-32013, f"commit_blocks/list failed: {err}") from err

    def _commit_blocks_override(self, params: dict) -> dict:
        """Phase 8 — explicit confirm required (never one-click)."""
        block_id = (params.get("id") or params.get("blockId") or "").strip()
        if not block_id:
            raise HandlerError(-32602, "commit_blocks/override requires id")
        confirmed = bool(params.get("confirm") or params.get("confirmed"))
        if not confirmed:
            raise HandlerError(
                -32026,
                "Override requires confirm=true (explicit acknowledgement).",
            )
        reason = (params.get("reason") or "").strip()
        try:
            from aider.z.uncertainty.commit_block_ledger import set_block_state

            updated = set_block_state(
                block_id,
                "overridden",
                repo_key=self.workspace_root,
                override_meta={
                    "by": "editor",
                    "reason": reason or "user override",
                    "confirmed": True,
                },
            )
            if updated is None:
                raise HandlerError(-32027, f"Commit block not found: {block_id}")
            self._notify(
                "gate/commit_updated",
                {"record": updated, "action": "overridden"},
            )
            return {"ok": True, "block": updated}
        except HandlerError:
            raise
        except Exception as err:
            raise HandlerError(-32013, f"commit_blocks/override failed: {err}") from err

    def _commit_blocks_resolve(self, params: dict) -> dict:
        """Mark a block resolved (checks passed / uncertainty cleared)."""
        block_id = (params.get("id") or params.get("blockId") or "").strip()
        if not block_id:
            raise HandlerError(-32602, "commit_blocks/resolve requires id")
        note = (params.get("note") or params.get("reason") or "").strip()
        try:
            from aider.z.uncertainty.commit_block_ledger import set_block_state

            updated = set_block_state(
                block_id,
                "resolved",
                repo_key=self.workspace_root,
                override_meta={
                    "by": "editor",
                    "note": note or "marked resolved",
                    "action": "resolve",
                },
            )
            if updated is None:
                raise HandlerError(-32027, f"Commit block not found: {block_id}")
            self._notify(
                "gate/commit_updated",
                {"record": updated, "action": "resolved"},
            )
            return {"ok": True, "block": updated}
        except HandlerError:
            raise
        except Exception as err:
            raise HandlerError(-32013, f"commit_blocks/resolve failed: {err}") from err

    def _mcp_list(self, params: dict) -> dict:
        """Merge local MCP store with cloud runtime (dedupe by server_name)."""
        del params
        try:
            from aider.z import mcp_local
            from aider.z.mcp_client import fetch_mcp_runtime

            local_rows = mcp_local.list_connections()
            cloud_rows: list[dict] = []
            try:
                cloud_rows = [t.public_dict() for t in fetch_mcp_runtime()]
            except Exception:
                cloud_rows = []

            by_name: dict[str, dict] = {}
            for row in cloud_rows:
                name = str(row.get("server_name") or row.get("serverName") or "")
                if not name:
                    continue
                merged = {
                    "id": row.get("id"),
                    "serverName": name,
                    "server_name": name,
                    "displayName": row.get("display_name") or row.get("displayName") or name,
                    "display_name": row.get("display_name") or row.get("displayName") or name,
                    "connectionType": row.get("connection_type")
                    or row.get("connectionType")
                    or "manual",
                    "enabled": bool(row.get("enabled", True)),
                    "status": row.get("status") or "connected",
                    "source": "cloud",
                    "config": row.get("config") or {},
                }
                by_name[name] = merged

            for row in local_rows:
                name = str(row.get("serverName") or "")
                if not name:
                    continue
                existing = by_name.get(name)
                local_view = {
                    **row,
                    "server_name": name,
                    "display_name": row.get("displayName"),
                    "source": "local" if not existing else "local+cloud",
                }
                if existing:
                    local_view["remoteId"] = existing.get("id")
                    local_view["cloudStatus"] = existing.get("status")
                by_name[name] = local_view

            connections = sorted(
                by_name.values(),
                key=lambda r: str(r.get("displayName") or r.get("serverName") or "").lower(),
            )
            return {"connections": connections}
        except Exception as err:
            raise HandlerError(-32014, f"mcp/list failed: {err}") from err

    def _mcp_catalog(self, params: dict) -> dict:
        del params
        try:
            from aider.z import mcp_local

            return {"catalog": mcp_local.catalog()}
        except Exception as err:
            raise HandlerError(-32030, f"mcp/catalog failed: {err}") from err

    def _mcp_connect(self, params: dict) -> dict:
        server_name = (params.get("serverName") or params.get("server_name") or "").strip()
        if not server_name:
            raise HandlerError(-32602, "mcp/connect requires serverName")
        try:
            from aider.z import mcp_local

            credentials = params.get("credentials") or {}
            if not isinstance(credentials, dict):
                raise HandlerError(-32602, "credentials must be an object")
            config = params.get("config") or {}
            if not isinstance(config, dict):
                raise HandlerError(-32602, "config must be an object")
            result = mcp_local.connect(
                server_name,
                credentials={str(k): str(v) for k, v in credentials.items()},
                config=config,
                display_name=params.get("displayName") or params.get("display_name"),
                scope=str(params.get("scope") or "personal"),
            )
            sync_cloud = params.get("syncCloud")
            if sync_cloud is None:
                sync_cloud = True
            if sync_cloud:
                try:
                    sync_result = mcp_local.sync_to_cloud()
                    result["sync"] = sync_result
                except Exception as sync_err:
                    result["sync"] = {"ok": False, "error": str(sync_err)}
            return result
        except ValueError as err:
            raise HandlerError(-32602, str(err)) from err
        except HandlerError:
            raise
        except Exception as err:
            raise HandlerError(-32031, f"mcp/connect failed: {err}") from err

    def _mcp_disconnect(self, params: dict) -> dict:
        connection_id = (params.get("id") or params.get("connectionId") or "").strip()
        if not connection_id:
            raise HandlerError(-32602, "mcp/disconnect requires id")
        try:
            from aider.z import mcp_local

            return mcp_local.disconnect(connection_id)
        except ValueError as err:
            raise HandlerError(-32602, str(err)) from err
        except Exception as err:
            raise HandlerError(-32032, f"mcp/disconnect failed: {err}") from err

    def _mcp_test(self, params: dict) -> dict:
        connection_id = (params.get("id") or params.get("connectionId") or "").strip()
        if connection_id:
            try:
                from aider.z import mcp_local

                return mcp_local.test_connection(connection_id, prefer_runtime=True)
            except Exception as err:
                raise HandlerError(-32033, f"mcp/test failed: {err}") from err

        # Ad-hoc test before save: { serverName, credentials, config }
        server_name = (params.get("serverName") or params.get("server_name") or "").strip()
        if not server_name:
            raise HandlerError(-32602, "mcp/test requires id or serverName")
        try:
            from aider.z import mcp_local

            skip_persist = bool(params.get("skipPersist", True))
            if skip_persist:
                entry = mcp_local.get_catalog_entry(server_name)
                if entry is None:
                    raise HandlerError(-32602, f"Unknown MCP server '{server_name}'")
                cfg = {**(entry.get("defaultConfig") or {}), **(params.get("config") or {})}
                url = str(cfg.get("url") or (params.get("credentials") or {}).get("url") or "")
                command = str(
                    cfg.get("command") or (params.get("credentials") or {}).get("command") or ""
                )
                credentials = params.get("credentials") or {}
                for field_def in entry.get("fields") or []:
                    if not isinstance(field_def, dict) or not field_def.get("required"):
                        continue
                    key = str(field_def.get("key") or "")
                    if key and not credentials.get(key) and key not in cfg:
                        return {"ok": False, "error": f"Missing required field: {key}"}
                # OAuth+PAT servers: token required for ad-hoc test when not oauth
                if entry.get("allowPatFallback") and not (
                    credentials.get("token") or credentials.get("access_token")
                ):
                    return {
                        "ok": False,
                        "error": "Provide a PAT for test, or use OAuth connect.",
                    }
                if url:
                    return mcp_local._test_http(url)
                if command:
                    import shutil
                    from pathlib import Path

                    exe = command.split()[0]
                    if shutil.which(exe) or Path(exe).exists():
                        return {"ok": True, "mode": "stdio", "command": command}
                    if exe in {"npx", "npm", "node"} and shutil.which("node"):
                        return {
                            "ok": True,
                            "mode": "stdio",
                            "command": command,
                            "note": "node available — full handshake runs after connect",
                        }
                    return {"ok": False, "error": f"Command not found: {exe}"}
                if credentials:
                    return {"ok": True, "mode": "credentials"}
                return {"ok": False, "error": "Nothing to test"}
            result = mcp_local.connect(
                server_name,
                credentials={
                    str(k): str(v) for k, v in (params.get("credentials") or {}).items()
                },
                config=params.get("config") or {},
            )
            return result.get("test") or {"ok": True}
        except HandlerError:
            raise
        except Exception as err:
            raise HandlerError(-32033, f"mcp/test failed: {err}") from err

    def _mcp_tools(self, params: dict) -> dict:
        connection_id = (params.get("id") or params.get("connectionId") or "").strip()
        try:
            from aider.z.mcp_runtime import get_session_manager, runtime_enabled

            if not runtime_enabled():
                return {"tools": [], "error": "MCP runtime disabled"}
            mgr = get_session_manager()
            if connection_id:
                tools = [t.public_dict() for t in mgr.list_tools(connection_id)]
                return {"connectionId": connection_id, "tools": tools}
            return {"tools": mgr.index_all_tools(spawn=False)}
        except Exception as err:
            raise HandlerError(-32038, f"mcp/tools failed: {err}") from err

    def _mcp_oauth_start(self, params: dict) -> dict:
        server_name = (params.get("serverName") or params.get("server_name") or "").strip()
        if not server_name:
            raise HandlerError(-32602, "mcp/oauthStart requires serverName")
        scope = str(params.get("scope") or "personal")
        try:
            from aider.z.auth import get_auth_base_url
            from aider.z.credentials import load_credentials
            import json as _json
            import urllib.error
            import urllib.parse
            import urllib.request

            creds = load_credentials()
            if creds is None or not getattr(creds, "access_token", None):
                raise HandlerError(-32039, "Sign in required for MCP OAuth")
            base = get_auth_base_url().rstrip("/")
            # Prefer JSON-friendly start: hit oauth/start and capture Location,
            # or build authorize URL client-side via catalog path.
            url = (
                f"{base}/v1/mcp/oauth/start?"
                + urllib.parse.urlencode(
                    {"server_name": server_name, "scope": scope, "client": "z-editor"}
                )
            )
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {creds.access_token}",
                    "Accept": "application/json",
                },
                method="GET",
            )
            # Do not follow redirects — we need the provider authorize URL
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
                    return None

            opener = urllib.request.build_opener(_NoRedirect)
            try:
                with opener.open(req, timeout=20) as resp:
                    body = resp.read().decode("utf-8")
                    if body.strip().startswith("{"):
                        data = _json.loads(body)
                        return {
                            "authorizeUrl": data.get("authorizeUrl")
                            or data.get("url")
                            or url,
                            "serverName": server_name,
                            "state": data.get("state"),
                        }
                    return {"authorizeUrl": resp.geturl(), "serverName": server_name}
            except urllib.error.HTTPError as err:
                if err.code in (301, 302, 303, 307, 308):
                    loc = err.headers.get("Location")
                    if loc:
                        return {"authorizeUrl": loc, "serverName": server_name}
                raise HandlerError(
                    -32039, f"oauthStart failed: HTTP {err.code} {err.read()[:200]!r}"
                ) from err
        except HandlerError:
            raise
        except Exception as err:
            raise HandlerError(-32039, f"mcp/oauthStart failed: {err}") from err

    def _mcp_confirm_first_use(self, params: dict) -> dict:
        server_name = (params.get("serverName") or params.get("server_name") or "").strip()
        tool_name = (params.get("toolName") or params.get("tool_name") or "*").strip() or "*"
        if not server_name:
            raise HandlerError(-32602, "mcp/confirmFirstUse requires serverName")
        try:
            from aider.z import mcp_local

            forever = params.get("forever")
            if forever is None:
                forever = True
            return mcp_local.mark_first_use_confirmed(
                server_name, tool_name, forever=bool(forever)
            )
        except Exception as err:
            raise HandlerError(-32034, f"mcp/confirmFirstUse failed: {err}") from err

    def _mcp_first_use_status(self, params: dict) -> dict:
        server_name = (params.get("serverName") or params.get("server_name") or "").strip()
        tool_name = (params.get("toolName") or params.get("tool_name") or "*").strip() or "*"
        if not server_name:
            raise HandlerError(-32602, "mcp/firstUseStatus requires serverName")
        try:
            from aider.z import mcp_local

            return mcp_local.first_use_status(server_name, tool_name)
        except Exception as err:
            raise HandlerError(-32035, f"mcp/firstUseStatus failed: {err}") from err

    def _mcp_sync(self, params: dict) -> dict:
        del params
        try:
            from aider.z import mcp_local

            return mcp_local.sync_to_cloud()
        except Exception as err:
            raise HandlerError(-32036, f"mcp/sync failed: {err}") from err

    def _auth_status(self, params: dict) -> dict:
        del params
        try:
            from aider.z.auth import current_session, get_auth_base_url
            from aider.z.onboarding import load_config

            creds = current_session()
            cfg = load_config()
            authed = bool(creds and creds.is_authenticated())
            email = None
            name = None
            if authed and creds and getattr(creds, "user", None):
                email = getattr(creds.user, "email", None)
                name = getattr(creds.user, "name", None)
            login = None
            try:
                from aider.z.app_server.login_session import controller

                login = controller.status().to_dict()
            except Exception:
                login = None
            return {
                "authenticated": authed,
                "email": email,
                "name": name,
                "displayName": creds.display_name() if authed and creds else None,
                "auth_mode": cfg.auth_mode,
                "selected_model": cfg.selected_model,
                "authBaseUrl": get_auth_base_url(),
                "login": login,
            }
        except Exception as err:
            raise HandlerError(-32015, f"auth/status failed: {err}") from err

    def _auth_login_start(self, params: dict) -> dict:
        try:
            from aider.z.app_server.login_session import controller

            return controller.start(
                method=(params.get("method") or "google"),
                intent=(params.get("intent") or "signin"),
                open_browser=bool(params.get("openBrowser", False)),
            )
        except ValueError as err:
            raise HandlerError(-32602, str(err)) from err
        except Exception as err:
            raise HandlerError(-32016, f"auth/loginStart failed: {err}") from err

    def _auth_login_status(self, params: dict) -> dict:
        del params
        from aider.z.app_server.login_session import controller

        return controller.status().to_dict()

    def _auth_login_cancel(self, params: dict) -> dict:
        del params
        from aider.z.app_server.login_session import controller

        return controller.cancel()

    def _auth_logout(self, params: dict) -> dict:
        del params
        try:
            from aider.z.auth import logout

            logout(io=None)
            return {"ok": True, "authenticated": False}
        except Exception as err:
            raise HandlerError(-32017, f"auth/logout failed: {err}") from err

    def _usage_summary(self, params: dict) -> dict:
        rng = (params.get("range") or "billing_period").strip()
        try:
            from aider.z.usage_client import fetch_usage_summary, normalize_for_profile

            raw = fetch_usage_summary(rng)
            return normalize_for_profile(raw)
        except Exception as err:
            raise HandlerError(-32037, f"usage/summary failed: {err}") from err

    def _turn_start(self, params: dict) -> dict:
        text = (params.get("text") or "").strip()
        thread_id = (params.get("threadId") or "default").strip() or "default"
        if not text:
            raise HandlerError(-32602, "turn/start requires text")
        if not self.workspace_root:
            raise HandlerError(
                -32020,
                "No workspace open — open a folder in Z Editor first",
            )
        try:
            return self._turn_manager().start(text=text, thread_id=thread_id)
        except ValueError as err:
            raise HandlerError(-32602, str(err)) from err
        except RuntimeError as err:
            raise HandlerError(-32021, str(err)) from err
        except Exception as err:
            raise HandlerError(-32022, f"turn/start failed: {err}") from err

    def _turn_respond(self, params: dict) -> dict:
        request_id = (params.get("requestId") or "").strip()
        if not request_id:
            raise HandlerError(-32602, "turn/respond requires requestId")
        thread_id = (params.get("threadId") or "").strip() or None
        ok = self._turn_manager().respond(
            request_id=request_id,
            response=params.get("response"),
            text=params.get("text"),
            thread_id=thread_id,
        )
        if not ok:
            raise HandlerError(-32023, "No matching waiting_input for requestId")
        return {"ok": True, "requestId": request_id}

    def _turn_cancel(self, params: dict) -> dict:
        thread_id = (params.get("threadId") or "default").strip() or "default"
        try:
            return self._turn_manager().cancel(thread_id=thread_id)
        except Exception as err:
            raise HandlerError(-32024, f"turn/cancel failed: {err}") from err


class HandlerError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
