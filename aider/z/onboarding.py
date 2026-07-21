"""Persisted first-run auth-mode + model choice.

V1 product default is **Z model router only**. BYOK remains available behind
``Z_ALLOW_BYOK=1`` for legacy/dev. Separate from z/credentials.py (account
tokens) and aider/onboarding.py (Aider OpenRouter prompts).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from .paths import byok_env_path, config_path, ensure_z_home


def byok_allowed() -> bool:
    """True when bring-your-own-key may still be offered (escape hatch)."""
    return os.environ.get("Z_ALLOW_BYOK", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@dataclass
class OnboardingConfig:
    auth_mode: Optional[str] = None  # "byok" | "router" | None (never chosen)
    selected_model: Optional[str] = None  # BYOK model id, or preferred router model


def load_config() -> OnboardingConfig:
    path = config_path()
    if not path.exists():
        return OnboardingConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return OnboardingConfig(
            auth_mode=data.get("auth_mode"),
            selected_model=data.get("selected_model"),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return OnboardingConfig()


def _merge_config(patch: dict) -> None:
    ensure_z_home()
    path = config_path()
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data.update(patch)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save_auth_mode(mode: str) -> None:
    _merge_config({"auth_mode": mode})


def save_selected_model(model_name: str) -> None:
    _merge_config({"selected_model": model_name})


def save_byok_key(env_var: str, api_key: str) -> None:
    ensure_z_home()
    path = byok_env_path()
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v
    existing[env_var] = api_key
    path.write_text(
        "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    os.environ[env_var] = api_key


def clear_setup(*, clear_keys: bool = True) -> None:
    """Forget BYOK/router mode + selected model (keep account login).

    Used by ``z reset`` / ``z auth switch`` so reinstalls don't trap you in
    an old model choice — account tokens stay in ~/.z/credentials.
    """
    ensure_z_home()
    path = config_path()
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data.pop("auth_mode", None)
    data.pop("selected_model", None)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if clear_keys:
        keys_path = byok_env_path()
        if keys_path.exists():
            try:
                # Drop known provider keys from the process env too.
                for line in keys_path.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.lstrip().startswith("#"):
                        k, _, _ = line.partition("=")
                        os.environ.pop(k.strip(), None)
                keys_path.unlink()
            except OSError:
                pass
