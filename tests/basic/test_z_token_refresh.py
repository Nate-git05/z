"""CLI auto-refresh when access token expires."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from aider.z.auth import current_session, refresh_access_token
from aider.z.credentials import Credentials, UserProfile


def test_refresh_access_token_persists_new_pair():
    old = Credentials(
        access_token="z_old",
        refresh_token="zref_old",
        expires_at=time.time() - 10,
        user=UserProfile(email="a@b.com", provider="email"),
    )

    payload = {
        "access_token": "z_new",
        "refresh_token": "zref_new",
        "expires_at": time.time() + 3600,
        "user": {"email": "a@b.com", "provider": "email"},
        "workspace": {"id": "ws1", "name": "Personal", "role": "owner"},
    }
    resp = MagicMock(status_code=200)
    resp.json.return_value = payload

    with patch("aider.z.auth.requests.post", return_value=resp), patch(
        "aider.z.auth.get_auth_base_url", return_value="https://auth.test"
    ), patch("aider.z.auth.save_credentials") as save:
        new = refresh_access_token(old)

    assert new is not None
    assert new.access_token == "z_new"
    assert new.refresh_token == "zref_new"
    save.assert_called_once()
    assert save.call_args.args[0].access_token == "z_new"


def test_current_session_auto_refreshes_expired_token():
    expired = Credentials(
        access_token="z_old",
        refresh_token="zref_old",
        expires_at=time.time() - 5,
        user=UserProfile(email="a@b.com", provider="email"),
    )
    fresh = Credentials(
        access_token="z_fresh",
        refresh_token="zref_fresh",
        expires_at=time.time() + 7200,
        user=UserProfile(email="a@b.com", provider="email"),
    )

    with patch("aider.z.auth.load_credentials", return_value=expired), patch(
        "aider.z.auth.refresh_access_token", return_value=fresh
    ) as refresh, patch(
        "aider.z.auth.apply_credentials_to_env", side_effect=lambda c: c
    ):
        session = current_session()

    assert session is not None
    assert session.access_token == "z_fresh"
    refresh.assert_called_once_with(expired)


def test_current_session_skips_refresh_when_still_valid():
    valid = Credentials(
        access_token="z_ok",
        refresh_token="zref_ok",
        expires_at=time.time() + 9999,
        user=UserProfile(email="a@b.com", provider="email"),
    )
    with patch("aider.z.auth.load_credentials", return_value=valid), patch(
        "aider.z.auth.refresh_access_token"
    ) as refresh, patch(
        "aider.z.auth.apply_credentials_to_env", side_effect=lambda c: c
    ):
        session = current_session()

    assert session is valid
    refresh.assert_not_called()
