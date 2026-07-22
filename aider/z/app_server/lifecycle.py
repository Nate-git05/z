"""Helpers for spawn/attach of z-app-server (shared semantics with the extension)."""

from __future__ import annotations

import os
import socket
from typing import Optional, Tuple
from urllib.parse import urlparse


DEFAULT_URL = "ws://127.0.0.1:8741"


def resolve_app_server_url(explicit: Optional[str] = None) -> str:
    return (
        (explicit or "").strip()
        or os.environ.get("Z_APP_SERVER_URL", "").strip()
        or DEFAULT_URL
    )


def parse_host_port(url: str) -> Tuple[str, int]:
    """Parse ``ws://host:port`` (or http) into (host, port)."""
    raw = (url or DEFAULT_URL).strip()
    if "://" not in raw:
        raw = "ws://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8741
    return host, int(port)


def port_is_open(host: str, port: int, *, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_reachable(url: Optional[str] = None) -> bool:
    host, port = parse_host_port(resolve_app_server_url(url))
    return port_is_open(host, port)
