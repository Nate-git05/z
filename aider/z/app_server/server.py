"""WebSocket JSON-RPC server for the Z Editor."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Optional

from aider.z.app_server.handlers import AppServerSession, HandlerError
from aider.z.app_server.protocol import make_error, make_result, parse_message

logger = logging.getLogger("z.app_server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8741


async def _handle_connection(websocket) -> None:
    session = AppServerSession()
    peer = getattr(websocket, "remote_address", None)
    logger.info("client connected %s", peer)
    try:
        async for raw in websocket:
            if not isinstance(raw, str):
                raw = raw.decode("utf-8", errors="replace")
            try:
                msg = parse_message(raw)
            except Exception as err:
                await websocket.send(
                    json.dumps(make_error(None, -32700, f"Parse error: {err}"))
                )
                continue

            req_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params")

            # Client notification — no response
            if req_id is None and method == "initialized":
                continue

            if not method:
                await websocket.send(
                    json.dumps(make_error(req_id, -32600, "Invalid Request"))
                )
                continue

            try:
                result = session.handle(method, params if isinstance(params, dict) else {})
                if req_id is not None:
                    await websocket.send(json.dumps(make_result(req_id, result)))
            except HandlerError as err:
                if req_id is not None:
                    await websocket.send(
                        json.dumps(make_error(req_id, err.code, err.message, err.data))
                    )
            except Exception as err:
                logger.exception("handler crash for %s", method)
                if req_id is not None:
                    await websocket.send(
                        json.dumps(make_error(req_id, -32000, f"Internal error: {err}"))
                    )
    finally:
        logger.info("client disconnected %s", peer)


async def _serve(host: str, port: int) -> None:
    try:
        from websockets.asyncio.server import serve
    except ImportError:
        try:
            from websockets.server import serve  # type: ignore  # websockets < 13
        except ImportError as err:
            raise SystemExit(
                "z-app-server requires the 'websockets' package.\n"
                "  pip install websockets\n"
                "  # or: pip install 'aider-chat[web]'"
            ) from err

    async with serve(_handle_connection, host, port):
        logger.info("z-app-server listening on ws://%s:%s", host, port)
        await asyncio.Future()  # run forever


def run_app_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    log_level: str = "INFO",
) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_serve(host, port))
    except KeyboardInterrupt:
        logger.info("shutting down")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="z app-server", description="Z Editor local IPC")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    run_app_server(host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
