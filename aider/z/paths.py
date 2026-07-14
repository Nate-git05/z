"""Filesystem paths for Z config and credentials."""

from __future__ import annotations

from pathlib import Path

Z_HOME = Path.home() / ".z"
CREDENTIALS_PATH = Z_HOME / "credentials"
CREDENTIALS_ENV_PATH = Z_HOME / "credentials.env"
CACHE_DIR = Z_HOME / "caches"


def ensure_z_home() -> Path:
    """Create ~/.z (mode 0700) if missing and return it."""
    Z_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        Z_HOME.chmod(0o700)
    except OSError:
        pass
    return Z_HOME
