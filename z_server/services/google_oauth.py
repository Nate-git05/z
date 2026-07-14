"""Google OAuth helpers for the CLI browser/loopback flow."""

from __future__ import annotations

import urllib.parse

import requests

from z_server.config import get_settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class GoogleOAuthError(Exception):
    pass


def build_google_authorize_url(
    *,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    settings = get_settings()
    if not settings.google_client_id:
        raise GoogleOAuthError("Z_GOOGLE_CLIENT_ID is not configured on the server.")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_google_code(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise GoogleOAuthError("Google OAuth client id/secret are not configured.")

    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise GoogleOAuthError(f"Google token exchange failed: {resp.text}")
    tokens = resp.json()
    access = tokens.get("access_token")
    if not access:
        raise GoogleOAuthError("Google token response missing access_token.")

    info = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access}"},
        timeout=30,
    )
    if info.status_code >= 400:
        raise GoogleOAuthError(f"Google userinfo failed: {info.text}")
    profile = info.json()
    return {
        "sub": profile.get("sub"),
        "email": profile.get("email"),
        "name": profile.get("name"),
        "email_verified": profile.get("email_verified"),
    }
