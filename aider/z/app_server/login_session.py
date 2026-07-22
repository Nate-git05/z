"""Non-blocking browser login for Z Editor (Phase 3).

Starts a ``WebAuthPageSession``, returns the login URL immediately, and finishes
credential persistence on a background thread so the WebSocket handler stays
responsive.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("z.app_server.login")


@dataclass
class LoginStatus:
    status: str  # idle | pending | succeeded | failed | cancelled
    method: Optional[str] = None
    login_url: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None
    email: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "method": self.method,
            "loginUrl": self.login_url,
            "state": self.state,
            "error": self.error,
            "email": self.email,
        }


class EditorLoginController:
    """Process-wide (per app-server) login coordinator."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = LoginStatus(status="idle")
        self._session = None  # WebAuthPageSession | None
        self._thread: Optional[threading.Thread] = None

    def status(self) -> LoginStatus:
        with self._lock:
            return LoginStatus(
                status=self._status.status,
                method=self._status.method,
                login_url=self._status.login_url,
                state=self._status.state,
                error=self._status.error,
                email=self._status.email,
            )

    def start(
        self,
        *,
        method: str = "google",
        intent: str = "signin",
        open_browser: bool = False,
    ) -> dict[str, Any]:
        method = (method or "google").strip().lower()
        if method not in ("google", "z"):
            raise ValueError("method must be 'google' or 'z'")
        intent = (intent or "signin").strip().lower()
        if intent not in ("signin", "signup"):
            intent = "signin"

        with self._lock:
            if self._status.status == "pending" and self._thread and self._thread.is_alive():
                return {
                    "started": True,
                    "busy": True,
                    **self._status.to_dict(),
                }

            from aider.z.auth import prepare_web_auth_page

            path = "/app/signup" if intent == "signup" else "/app/login"
            session = prepare_web_auth_page(
                path=path,
                extra_params={"method": method, "intent": intent},
                success_html=(
                    "You're signed in to Z. Return to Z Editor — this tab can close."
                ),
                failure_label="Sign in",
            )
            self._session = session
            self._status = LoginStatus(
                status="pending",
                method=method,
                login_url=session.page_url,
                state=session.state,
            )

            if open_browser:
                try:
                    import webbrowser

                    webbrowser.open(session.page_url)
                except Exception:
                    pass

            self._thread = threading.Thread(
                target=self._finish,
                args=(session, method),
                daemon=True,
                name="z-editor-login",
            )
            self._thread.start()
            return {
                "started": True,
                "busy": False,
                **self._status.to_dict(),
            }

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            session = self._session
            self._session = None
            if self._status.status == "pending":
                self._status = LoginStatus(status="cancelled", method=self._status.method)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        return self.status().to_dict()

    def _finish(self, session, method: str) -> None:
        from aider.z.auth import (
            AuthError,
            _credentials_from_api_payload,
            _persist_session_credentials,
        )

        class _QuietIO:
            def tool_output(self, *args, **kwargs):
                return None

            def tool_error(self, *args, **kwargs):
                return None

        try:
            finished = session.wait()
            if not finished:
                with self._lock:
                    self._status = LoginStatus(
                        status="failed",
                        method=method,
                        error="Timed out waiting for browser sign-in.",
                    )
                return
            if session.error:
                with self._lock:
                    self._status = LoginStatus(
                        status="failed", method=method, error=session.error
                    )
                return
            data = session.data or {}
            user = data.get("user") if isinstance(data.get("user"), dict) else {}
            try:
                result = _credentials_from_api_payload(
                    data, provider=user.get("provider") or "web"
                )
            except AuthError as err:
                with self._lock:
                    self._status = LoginStatus(
                        status="failed", method=method, error=str(err)
                    )
                return
            if not result.ok or not result.credentials:
                with self._lock:
                    self._status = LoginStatus(
                        status="failed",
                        method=method,
                        error=result.message or "Authentication failed.",
                    )
                return
            creds = _persist_session_credentials(_QuietIO(), result.credentials)
            email = None
            if creds.user:
                email = getattr(creds.user, "email", None)
            with self._lock:
                self._status = LoginStatus(
                    status="succeeded",
                    method=method,
                    email=email or creds.display_name(),
                )
        except Exception as err:
            logger.exception("editor login failed")
            with self._lock:
                self._status = LoginStatus(
                    status="failed", method=method, error=str(err)
                )
        finally:
            try:
                session.close()
            except Exception:
                pass
            with self._lock:
                if self._session is session:
                    self._session = None


# Singleton for the app-server process
controller = EditorLoginController()
