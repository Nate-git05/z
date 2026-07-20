"""Vercel serverless entrypoint for the Z FastAPI app."""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root on PYTHONPATH so `z_server` imports resolve in the Vercel build.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from z_server.app import app  # noqa: E402, F401
