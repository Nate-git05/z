"""WebSocket JSON-RPC server for the Z Editor."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import logging
import os
from pathlib import Path
from typing import Optional

from aider.z.app_server.handlers import AppServerSession, HandlerError
from aider.z.app_server.protocol import make_error, make_notification, make_result, parse_message

logger = logging.getLogger("z.app_server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8741


def _write_pid_file(path: Optional[str]) -> None:
    if not path:
        return
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()), encoding="utf-8")

    def _cleanup() -> None:
        try:
            if p.is_file() and p.read_text(encoding="utf-8").strip() == str(os.getpid()):
                p.unlink(missing_ok=True)
        except OSError:
            pass

    atexit.register(_cleanup)


async def _handle_connection(websocket) -> None:
    loop = asyncio.get_running_loop()
    outbound: asyncio.Queue = asyncio.Queue()

    def notify(method: str, params: Optional[dict] = None) -> None:
        msg = make_notification(method, params)

        def _put() -> None:
            outbound.put_nowait(msg)

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            # Loop closed
            pass

    session = AppServerSession(notify=notify)
    peer = getattr(websocket, "remote_address", None)
    logger.info("client connected %s", peer)

    async def _sender() -> None:
        while True:
            msg = await outbound.get()
            try:
                await websocket.send(json.dumps(msg))
            except Exception:
                logger.debug("send failed", exc_info=True)
                return

    sender_task = asyncio.create_task(_sender())
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
                # Handlers are sync / start background work — never block the loop long.
                result = await asyncio.to_thread(
                    session.handle,
                    method,
                    params if isinstance(params, dict) else {},
                )
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
        try:
            session.dispose()
        except Exception:
            logger.debug("session dispose failed", exc_info=True)
        sender_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass
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
    pid_file: Optional[str] = None,
) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _write_pid_file(pid_file)
    try:
        asyncio.run(_serve(host, port))
    except KeyboardInterrupt:
        logger.info("shutting down")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="z app-server", description="Z Editor local IPC")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--pid-file",
        default=None,
        help="Write PID for Z Editor spawn/attach lifecycle (optional)",
    )
    args = parser.parse_args(argv)
    run_app_server(
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        pid_file=getattr(args, "pid_file", None),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
