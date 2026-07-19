"""Filesystem paths for Z config and credentials."""

from __future__ import annotations

import os
from pathlib import Path


def _z_home() -> Path:
    override = os.environ.get("Z_HOME")
    if override:
        return Path(override)
    return Path.home() / ".z"


# Resolved at import; tests that set Z_HOME before importing are fine.
# Callers that need a live override should use ensure_z_home() / skills_dir().
Z_HOME = _z_home()
CREDENTIALS_PATH = Z_HOME / "credentials"
CREDENTIALS_ENV_PATH = Z_HOME / "credentials.env"
# First-run BYOK/router choice + selected model (not account tokens).
CONFIG_PATH = Z_HOME / "config.json"
# Provider API keys for BYOK — separate from credentials.env (Z-account tokens).
BYOK_ENV_PATH = Z_HOME / "byok.env"
CACHE_DIR = Z_HOME / "caches"
SKILLS_DIR = Z_HOME / "skills"


def ensure_z_home() -> Path:
    """Create ~/.z (mode 0700) if missing and return it (honors Z_HOME)."""
    home = _z_home()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        home.chmod(0o700)
    except OSError:
        pass
    return home


def config_path() -> Path:
    """Live path to config.json (honors current Z_HOME)."""
    return _z_home() / "config.json"


def byok_env_path() -> Path:
    """Live path to byok.env (honors current Z_HOME)."""
    return _z_home() / "byok.env"
