"""Z account authentication — email OTP, Twilio Verify phone, Google browser OAuth.

Account auth is separate from model API keys (BYOK). Tokens persist in ~/.z/credentials
for workspace/team features (uncertainty sharing, escalation routing, etc.).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import os
import secrets
import socketserver
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import requests

from .credentials import (
    Credentials,
    UserProfile,
    WorkspaceContext,
    apply_credentials_to_env,
    clear_credentials,
    load_credentials,
    save_credentials,
)

# Auth API base. Override with Z_AUTH_URL. When unset / unreachable, Z_AUTH_DEV
# enables a local mock so the CLI UX can be exercised without a backend.
DEFAULT_AUTH_URL = os.environ.get("Z_AUTH_URL", "https://auth.z.dev")
GOOGLE_CLIENT_ID = os.environ.get("Z_GOOGLE_CLIENT_ID", "")
AUTH_TIMEOUT_SECONDS = 300


@dataclass
class AuthResult:
    ok: bool
    credentials: Credentials | None = None
    message: str = ""


class AuthError(Exception):
    pass


def auth_dev_mode() -> bool:
    """True when we should use the local mock auth backend."""
    flag = os.environ.get("Z_AUTH_DEV", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    # Auto-enable when no real auth URL / client id is configured
    return not os.environ.get("Z_AUTH_URL") and not GOOGLE_CLIENT_ID


def get_auth_base_url() -> str:
    return os.environ.get("Z_AUTH_URL", DEFAULT_AUTH_URL).rstrip("/")


def current_session() -> Credentials | None:
    return apply_credentials_to_env(load_credentials())


def require_account(io, *, feature: str = "this feature") -> Credentials | None:
    """
    Ensure the user is logged into a Z account. If not, prompt with the three auth options.
    Returns credentials on success, None if the user declined / failed.
    """
    creds = current_session()
    if creds and creds.is_authenticated():
        return creds
    io.tool_output(f"A Z account is required for {feature}.")
    return run_login_flow(io)


def run_login_flow(io, analytics=None) -> Credentials | None:
    """Interactive sign-in: branded login screen → Google / Email / Phone."""
    from .login_screen import prompt_login_choice

    version = ""
    try:
        from aider import __version__

        version = f"v{__version__}"
    except Exception:
        pass

    status_message = "Z auth dev mode — codes are accepted locally." if auth_dev_mode() else ""

    provider = prompt_login_choice(io, version=version, status_message=status_message)
    if provider is None:
        io.tool_output("Login cancelled.")
        return None

    try:
        if provider == "email":
            result = login_with_email(io)
        elif provider == "phone":
            result = login_with_phone(io)
        elif provider == "google":
            result = login_with_google(io, analytics=analytics)
        else:
            io.tool_error(f"Unknown option: {provider}")
            return None
    except AuthError as err:
        io.tool_error(str(err))
        if analytics:
            analytics.event("z_auth_failure", error=str(err))
        return None
    except KeyboardInterrupt:
        io.tool_output("\nLogin cancelled.")
        return None

    if not result.ok or not result.credentials:
        io.tool_error(result.message or "Authentication failed.")
        if analytics:
            analytics.event("z_auth_failure", message=result.message)
        return None

    save_credentials(result.credentials)
    apply_credentials_to_env(result.credentials)
    from .paths import CREDENTIALS_PATH

    io.tool_output("")
    io.tool_output(f"Signed in as {result.credentials.display_name()}.")
    if result.credentials.workspace and result.credentials.workspace.name:
        io.tool_output(f"Workspace: {result.credentials.workspace.name}")
    io.tool_output(f"Credentials saved to {CREDENTIALS_PATH}")
    if analytics:
        provider = (
            (result.credentials.user.provider if result.credentials.user else None) or "unknown"
        )
        analytics.event("z_auth_success", provider=provider)
    return result.credentials


def logout(io=None) -> None:
    clear_credentials()
    if io:
        io.tool_output("Signed out of Z. Local credentials cleared.")


# ---------------------------------------------------------------------------
# Email magic-link / OTP
# ---------------------------------------------------------------------------


def login_with_email(io) -> AuthResult:
    email = (io.prompt_ask("Email address") or "").strip()
    if not email or "@" not in email:
        raise AuthError("A valid email address is required.")
    name = (io.prompt_ask("Your name", default="") or "").strip() or None

    if auth_dev_mode():
        return _dev_email_login(io, email, name)

    base = get_auth_base_url()
    try:
        resp = requests.post(
            f"{base}/v1/auth/email/start",
            json={"email": email, "name": name},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as err:
        raise AuthError(f"Could not start email sign-in: {err}") from err

    method = (payload.get("method") or "otp").lower()
    if method == "magic_link":
        io.tool_output(
            "Check your email for a magic link,"
            " then press Enter here once you've opened it."
        )
        io.prompt_ask("Press Enter after confirming", default="")
        session_id = payload.get("session_id")
        return _poll_email_session(io, session_id, email, name)

    io.tool_output("We sent a one-time code to your email.")
    code = (io.prompt_ask("Enter the code") or "").strip()
    if not code:
        raise AuthError("No code entered.")
    return _verify_email_code(email, code, name)


def _verify_email_code(email: str, code: str, name: str | None) -> AuthResult:
    base = get_auth_base_url()
    try:
        resp = requests.post(
            f"{base}/v1/auth/email/verify",
            json={"email": email, "code": code, "name": name},
            timeout=30,
        )
        resp.raise_for_status()
        return _credentials_from_api_payload(resp.json(), provider="email", email=email, name=name)
    except requests.RequestException as err:
        raise AuthError(f"Email verification failed: {err}") from err


def _poll_email_session(io, session_id, email, name) -> AuthResult:
    if not session_id:
        raise AuthError("Auth server did not return a session id for magic-link login.")
    base = get_auth_base_url()
    deadline = time.time() + AUTH_TIMEOUT_SECONDS
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{base}/v1/auth/email/session/{session_id}",
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "confirmed":
                    return _credentials_from_api_payload(
                        data, provider="email", email=email, name=name
                    )
        except requests.RequestException:
            pass
        time.sleep(2)
    raise AuthError("Timed out waiting for email confirmation.")


def _dev_email_login(io, email: str, name: str | None) -> AuthResult:
    io.tool_warning("Z auth dev mode — no email will be sent.")
    io.tool_output("Enter any 6-digit code (dev accepts 000000).")
    code = (io.prompt_ask("Enter the code", default="000000") or "").strip()
    if code != "000000" and not (code.isdigit() and len(code) == 6):
        # Still accept any 6-digit in dev for UX flexibility
        if not (code.isdigit() and len(code) >= 4):
            raise AuthError("Invalid code.")
    return AuthResult(
        ok=True,
        credentials=_mint_dev_credentials(provider="email", email=email, name=name),
        message="dev email ok",
    )


# ---------------------------------------------------------------------------
# Phone — Twilio Verify (server-side)
# ---------------------------------------------------------------------------


def login_with_phone(io) -> AuthResult:
    phone = (io.prompt_ask("Phone number (E.164, e.g. +15551234567)") or "").strip()
    if not phone.startswith("+") or len(phone) < 8:
        raise AuthError("Enter a phone number in E.164 format, e.g. +15551234567.")

    if auth_dev_mode():
        return _dev_phone_login(io, phone)

    base = get_auth_base_url()
    try:
        resp = requests.post(
            f"{base}/v1/auth/phone/start",
            json={"phone": phone},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as err:
        raise AuthError(f"Could not start phone verification: {err}") from err

    io.tool_output("We sent a verification code via SMS (Twilio Verify).")
    code = (io.prompt_ask("Enter the SMS code") or "").strip()
    if not code:
        raise AuthError("No code entered.")

    try:
        resp = requests.post(
            f"{base}/v1/auth/phone/verify",
            json={"phone": phone, "code": code},
            timeout=30,
        )
        resp.raise_for_status()
        return _credentials_from_api_payload(resp.json(), provider="phone", phone=phone)
    except requests.RequestException as err:
        raise AuthError(f"Phone verification failed: {err}") from err


def _dev_phone_login(io, phone: str) -> AuthResult:
    io.tool_warning("Z auth dev mode — no SMS will be sent.")
    io.tool_output("Enter any code (dev accepts 000000).")
    code = (io.prompt_ask("Enter the SMS code", default="000000") or "").strip()
    if not code:
        raise AuthError("No code entered.")
    return AuthResult(
        ok=True,
        credentials=_mint_dev_credentials(provider="phone", phone=phone),
        message="dev phone ok",
    )


# ---------------------------------------------------------------------------
# Google — browser / loopback OAuth (gh auth login style)
# ---------------------------------------------------------------------------


def login_with_google(io, analytics=None) -> AuthResult:
    """
    Open a browser to complete Google sign-in, listen on localhost for the
    redirect, then exchange the code with the Z auth backend for a session token.
    """
    if auth_dev_mode() and not GOOGLE_CLIENT_ID:
        return _dev_google_login(io)

    port = _find_available_port(8765, 8865)
    if port is None:
        raise AuthError("Could not find a free local port for the OAuth callback.")

    redirect_uri = f"http://127.0.0.1:{port}/callback"
    state = secrets.token_urlsafe(24)
    code_verifier, code_challenge = _generate_pkce()

    result_box: dict = {"code": None, "error": None, "done": threading.Event()}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = parse_qs(parsed.query)
            if qs.get("state", [None])[0] != state:
                result_box["error"] = "Invalid OAuth state"
                self._respond(400, "Invalid state. You can close this tab.")
                result_box["done"].set()
                return
            if "error" in qs:
                result_box["error"] = qs["error"][0]
                self._respond(400, f"Auth error: {qs['error'][0]}. You can close this tab.")
                result_box["done"].set()
                return
            result_box["code"] = qs.get("code", [None])[0]
            self._respond(200, "Signed in to Z. You can close this tab and return to the terminal.")
            result_box["done"].set()

        def _respond(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = f"<html><body><h2>{body}</h2></body></html>"
            self.wfile.write(html.encode("utf-8"))

        def log_message(self, format, *args):  # noqa: A003
            return

    server = socketserver.TCPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Prefer Z auth backend's Google start URL (it holds the client secret).
    # Fall back to direct Google OAuth if Z_GOOGLE_CLIENT_ID is set.
    auth_url = _build_google_auth_url(
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
    )
    io.tool_output("Opening browser for Google sign-in…")
    io.tool_output(f"If it doesn't open, visit:\n  {auth_url}")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    if analytics:
        analytics.event("z_auth_google_browser_opened")

    finished = result_box["done"].wait(timeout=AUTH_TIMEOUT_SECONDS)
    server.shutdown()
    server.server_close()

    if not finished:
        raise AuthError("Timed out waiting for Google sign-in.")
    if result_box["error"]:
        raise AuthError(f"Google sign-in failed: {result_box['error']}")
    code = result_box["code"]
    if not code:
        raise AuthError("Google sign-in did not return an authorization code.")

    return _exchange_google_code(code, code_verifier, redirect_uri)


def _build_google_auth_url(*, redirect_uri: str, state: str, code_challenge: str) -> str:
    base = get_auth_base_url()
    # Backend-hosted start URL keeps the Google client secret server-side
    params = {
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if not auth_dev_mode():
        return f"{base}/v1/auth/google/start?{urllib.parse.urlencode(params)}"

    if not GOOGLE_CLIENT_ID:
        raise AuthError("Z_GOOGLE_CLIENT_ID is required for Google OAuth outside dev mode.")
    google_params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(google_params)


def _exchange_google_code(code: str, code_verifier: str, redirect_uri: str) -> AuthResult:
    base = get_auth_base_url()
    try:
        resp = requests.post(
            f"{base}/v1/auth/google/exchange",
            json={
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return _credentials_from_api_payload(resp.json(), provider="google")
    except requests.RequestException as err:
        raise AuthError(f"Google token exchange failed: {err}") from err


def _dev_google_login(io) -> AuthResult:
    """Simulate the browser flow with a local success page (no Google client id)."""
    port = _find_available_port(8765, 8865)
    if port is None:
        raise AuthError("Could not find a free local port for the OAuth callback.")

    done = threading.Event()
    profile: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            profile["email"] = qs.get("email", ["dev.user@example.com"])[0]
            profile["name"] = qs.get("name", ["Dev User"])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Signed in to Z (dev).</h2>"
                b"<p>You can close this tab.</p></body></html>"
            )
            done.set()

        def log_message(self, format, *args):  # noqa: A003
            return

    server = socketserver.TCPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/callback?email=dev.user%40example.com&name=Dev%20User"
    io.tool_warning("Z auth dev mode — opening a local callback (no Google).")
    io.tool_output(f"If the browser doesn't open, visit:\n  {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    # Also allow confirming from the terminal if the browser can't open
    if not done.wait(timeout=8):
        io.tool_output("Browser did not complete; confirming from the terminal.")
        if io.confirm_ask("Complete Google (dev) sign-in now?", default="y"):
            profile.setdefault("email", "dev.user@example.com")
            profile.setdefault("name", "Dev User")
            done.set()

    server.shutdown()
    server.server_close()

    if not done.is_set():
        raise AuthError("Timed out waiting for Google (dev) sign-in.")

    return AuthResult(
        ok=True,
        credentials=_mint_dev_credentials(
            provider="google",
            email=profile.get("email", "dev.user@example.com"),
            name=profile.get("name", "Dev User"),
        ),
        message="dev google ok",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _credentials_from_api_payload(
    data: dict,
    *,
    provider: str,
    email: str | None = None,
    name: str | None = None,
    phone: str | None = None,
) -> AuthResult:
    token = data.get("access_token") or data.get("token")
    if not token:
        raise AuthError("Auth server response missing access_token.")
    user_data = data.get("user") or {}
    ws_data = data.get("workspace") or {}
    expires_in = data.get("expires_in")
    expires_at = time.time() + float(expires_in) if expires_in else data.get("expires_at")
    creds = Credentials(
        access_token=token,
        refresh_token=data.get("refresh_token"),
        token_type=data.get("token_type") or "Bearer",
        expires_at=expires_at,
        user=UserProfile(
            id=user_data.get("id"),
            email=user_data.get("email") or email,
            name=user_data.get("name") or name,
            phone=user_data.get("phone") or phone,
            provider=user_data.get("provider") or provider,
        ),
        workspace=WorkspaceContext(
            id=ws_data.get("id"),
            name=ws_data.get("name"),
            role=ws_data.get("role"),
            organization=ws_data.get("organization"),
        ),
    )
    return AuthResult(ok=True, credentials=creds)


def _mint_dev_credentials(
    *,
    provider: str,
    email: str | None = None,
    name: str | None = None,
    phone: str | None = None,
) -> Credentials:
    token = "zdev_" + secrets.token_urlsafe(24)
    return Credentials(
        access_token=token,
        refresh_token="zdev_refresh_" + secrets.token_urlsafe(12),
        token_type="Bearer",
        expires_at=time.time() + 86400 * 30,
        user=UserProfile(
            id="dev-" + secrets.token_hex(4),
            email=email,
            name=name or (email.split("@")[0] if email else None),
            phone=phone,
            provider=provider,
        ),
        workspace=WorkspaceContext(id="ws-dev", name="Personal", role="owner"),
    )


def _generate_pkce() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
    return code_verifier, code_challenge


def _find_available_port(start: int, end: int) -> int | None:
    for port in range(start, end + 1):
        try:
            with socketserver.TCPServer(("127.0.0.1", port), None):
                return port
        except OSError:
            continue
    return None


def prompt_byok_setup(io) -> bool:
    """Pick a foundation-model family, then a specific model within it,
    then prompt only for the env var(s) that model actually needs."""
    from .models_catalog import CURATED_SECTIONS

    io.tool_output("")
    io.tool_output("Which foundation model do you want to use?")
    for i, (title, _models) in enumerate(CURATED_SECTIONS):
        io.tool_output(f"  [{i + 1}] {title}")
    io.tool_output(f"  [{len(CURATED_SECTIONS) + 1}] Other / type a model name")

    choice = (io.prompt_ask("Choose", default="1") or "").strip()
    try:
        idx = int(choice) - 1
    except ValueError:
        idx = -1

    if 0 <= idx < len(CURATED_SECTIONS):
        title, models = CURATED_SECTIONS[idx]
        io.tool_output("")
        io.tool_output(f"{title} models:")
        for j, name in enumerate(models):
            io.tool_output(f"  [{j + 1}] {name}")
        model_choice = (io.prompt_ask("Choose a model", default="1") or "").strip()
        try:
            model_name = models[int(model_choice) - 1]
        except (ValueError, IndexError):
            io.tool_error("Not a valid choice.")
            return False
    else:
        model_name = (
            io.prompt_ask("Type the exact model name", default="") or ""
        ).strip()
        if not model_name:
            io.tool_error("No model entered.")
            return False

    from aider.models import Model, fuzzy_match_models

    matches = fuzzy_match_models(model_name)
    if model_name not in matches:
        if matches:
            io.tool_error(
                f"'{model_name}' is not a recognized model. Did you mean: "
                f"{', '.join(matches[:3])}?"
            )
        else:
            io.tool_error(f"'{model_name}' is not a recognized model.")
        return False

    # Only construct Model() after fuzzy_match_models confirms it's real —
    # Model() alone does not validate existence (fake names look "set up").
    model = Model(model_name)

    from .onboarding import save_byok_key, save_selected_model

    if not model.missing_keys and model.keys_in_environment:
        io.tool_output(f"'{model_name}' already has its required key(s) set.")
    else:
        for env_var in model.missing_keys:
            key = io.prompt_ask(f"Paste your {env_var}", default="")
            if not key or not key.strip():
                io.tool_error("No key entered.")
                return False
            save_byok_key(env_var, key.strip())

    save_selected_model(model_name)
    io.tool_output(f"Saved. Using {model_name}.")
    return True


def whoami_text(creds: Credentials | None = None) -> str:
    creds = creds or current_session()
    if not creds or not creds.is_authenticated():
        return "Not signed in. Run `z` (or `z login`) to sign in."
    lines = [f"Signed in as {creds.display_name()}"]
    if creds.user:
        if creds.user.email:
            lines.append(f"  email: {creds.user.email}")
        if creds.user.phone:
            lines.append(f"  phone: {creds.user.phone}")
        if creds.user.provider:
            lines.append(f"  provider: {creds.user.provider}")
    if creds.workspace and (creds.workspace.name or creds.workspace.id):
        ws = creds.workspace.name or creds.workspace.id
        role = f" ({creds.workspace.role})" if creds.workspace.role else ""
        lines.append(f"  workspace: {ws}{role}")
        if creds.workspace.organization:
            lines.append(f"  organization: {creds.workspace.organization}")
    lines.append(
        "  (Model API keys are separate — use `z auth switch` or set provider env vars.)"
    )
    return "\n".join(lines)
