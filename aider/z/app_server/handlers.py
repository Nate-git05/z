"""Request handlers for z-app-server IPC v0.

V0 focuses on read surfaces + stubs. Turn execution (full Coder.run_one) lands
in a later phase once the gateway stream path is green.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from aider.z.app_server.protocol import PROTOCOL_VERSION, SERVER_NAME


class AppServerSession:
    """Per-connection session state."""

    def __init__(self) -> None:
        self.initialized = False
        self.client_info: dict[str, Any] = {}
        self.workspace_root: Optional[str] = None

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
        if method == "skills/list":
            return self._skills_list(params)
        if method == "skills/create":
            return self._skills_create(params)
        if method == "commit_blocks/list":
            return self._commit_blocks_list(params)
        if method == "mcp/list":
            return self._mcp_list(params)
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
            return self._turn_start_stub(params)
        raise HandlerError(-32601, f"Method not found: {method}")

    def _initialize(self, params: dict) -> dict:
        self.client_info = dict(params.get("clientInfo") or {})
        root = params.get("workspaceRoot")
        if root:
            self.workspace_root = str(root)
        self.initialized = True
        z_home = os.environ.get("Z_HOME") or str(Path.home() / ".z")
        return {
            "serverInfo": {"name": SERVER_NAME, "version": PROTOCOL_VERSION},
            "zHome": z_home,
            "capabilities": [
                "uncertainty",
                "skills",
                "commit_blocks",
                "mcp",
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
        return {"ok": True, "root": self.workspace_root}

    def _workspace_info(self, params: dict) -> dict:
        del params
        root = self.workspace_root
        return {
            "root": root,
            "open": bool(root),
            "name": Path(root).name if root else None,
        }

    def _uncertainty_list(self, params: dict) -> dict:
        sort = (params.get("sort") or "risk").strip().lower()
        nodes: list[dict] = []
        try:
            from aider.z.uncertainty.store import UncertaintyStore
            from aider.z.uncertainty.schema import TIER_RANK

            store = UncertaintyStore(root=self.workspace_root)
            items = list(store.nodes.values())
            if sort == "age":
                items.sort(key=lambda n: n.created_at or "", reverse=True)
            elif sort == "type":
                items.sort(key=lambda n: (n.type.value, n.title or ""))
            else:
                items.sort(
                    key=lambda n: (
                        TIER_RANK.get(n.risk_tier, 99),
                        n.created_at or "",
                    )
                )
            nodes = [n.to_dict() for n in items]
        except Exception as err:
            raise HandlerError(-32010, f"uncertainty/list failed: {err}") from err
        return {"nodes": nodes, "sort": sort}

    def _skills_list(self, params: dict) -> dict:
        kind = (params.get("kind") or "").strip() or None
        quality = (params.get("quality_state") or "").strip() or None
        query = (params.get("query") or "").strip().lower() or None
        skills: list[dict] = []
        try:
            from aider.z.skills.store import LocalSkillStore

            for s in LocalSkillStore().list_skills():
                if kind and (getattr(s, "kind", None) or "") != kind:
                    continue
                if quality and (getattr(s, "quality_state", None) or "") != quality:
                    continue
                blob = " ".join(
                    [
                        getattr(s, "title", "") or "",
                        getattr(s, "description", "") or "",
                        " ".join(getattr(s, "triggers", None) or []),
                        getattr(s, "capability", "") or "",
                    ]
                ).lower()
                if query and query not in blob:
                    continue
                skills.append(
                    {
                        "id": getattr(s, "id", None),
                        "title": getattr(s, "title", None),
                        "kind": getattr(s, "kind", None),
                        "description": getattr(s, "description", None),
                        "triggers": list(getattr(s, "triggers", None) or []),
                        "capability": getattr(s, "capability", None),
                        "quality_state": getattr(s, "quality_state", None),
                        "needs_review": bool(getattr(s, "needs_review", False)),
                        "source": getattr(s, "source", None),
                    }
                )
        except Exception as err:
            raise HandlerError(-32011, f"skills/list failed: {err}") from err
        return {"skills": skills}

    def _skills_create(self, params: dict) -> dict:
        draft = params.get("skill") or {}
        if not isinstance(draft, dict):
            raise HandlerError(-32602, "skills/create requires skill object")
        try:
            from aider.z.skills.schema import Skill
            from aider.z.skills.store import LocalSkillStore

            title = (draft.get("title") or draft.get("name") or "").strip()
            if not title:
                raise HandlerError(-32602, "skill.title is required")
            skill = Skill(
                title=title,
                description=(draft.get("description") or "").strip(),
                content=(draft.get("content") or draft.get("body") or "").strip(),
                kind=(draft.get("kind") or "playbook").strip() or "playbook",
                triggers=list(draft.get("triggers") or []),
                capability=(draft.get("capability") or "").strip(),
                source="manual",
                quality_state="draft",
                needs_review=True,
            )
            LocalSkillStore().save(skill)
            return {"skill": skill.to_dict()}
        except HandlerError:
            raise
        except Exception as err:
            raise HandlerError(-32012, f"skills/create failed: {err}") from err

    def _commit_blocks_list(self, params: dict) -> dict:
        del params
        try:
            from aider.z.uncertainty.commit_block_ledger import list_blocks

            blocks = list_blocks(repo_key=self.workspace_root)
            return {"blocks": blocks}
        except Exception as err:
            raise HandlerError(-32013, f"commit_blocks/list failed: {err}") from err

    def _mcp_list(self, params: dict) -> dict:
        del params
        try:
            from aider.z.mcp_client import fetch_mcp_runtime

            tools = fetch_mcp_runtime()
            return {"connections": [t.public_dict() for t in tools]}
        except Exception as err:
            raise HandlerError(-32014, f"mcp/list failed: {err}") from err

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
        # V0 stub — Phase 9 fills from gateway /v1/gateway/usage
        rng = (params.get("range") or "billing_period").strip()
        return {"range": rng, "byModel": [], "note": "gateway usage not wired yet"}

    def _turn_start_stub(self, params: dict) -> dict:
        text = (params.get("text") or "").strip()
        thread_id = (params.get("threadId") or "default").strip() or "default"
        if not text:
            raise HandlerError(-32602, "turn/start requires text")
        # Full agent loop is Phase 4 — accept and report stub.
        import uuid

        turn_id = str(uuid.uuid4())
        return {
            "turnId": turn_id,
            "threadId": thread_id,
            "accepted": True,
            "stub": True,
            "message": (
                "Turn accepted (stub). Wire Coder.run_one + gateway streaming in Phase 4."
            ),
        }


class HandlerError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
