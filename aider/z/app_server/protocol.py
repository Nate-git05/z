"""JSON-RPC helpers for z-app-server IPC v0."""

from __future__ import annotations

import json
from typing import Any, Optional


PROTOCOL_VERSION = "0.1.0"
SERVER_NAME = "z-app-server"


def make_result(id_: Any, result: Any) -> dict:
    return {"id": id_, "result": result}


def make_error(id_: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"id": id_, "error": err}


def make_notification(method: str, params: Optional[dict] = None) -> dict:
    msg: dict[str, Any] = {"method": method}
    if params is not None:
        msg["params"] = params
    return msg


def parse_message(raw: str) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON-RPC message must be an object")
    return data
