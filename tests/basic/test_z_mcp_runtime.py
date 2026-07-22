"""Phase 11 — MCP SessionManager + turn fence protocol."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


class FakeMcpServer:
    """Minimal Content-Length MCP server for unit tests."""

    def __init__(self):
        self.tools = [
            {
                "name": "echo",
                "description": "Echo arguments",
                "inputSchema": {"type": "object"},
            }
        ]

    def handle(self, msg: dict) -> dict | None:
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "0"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": self.tools}}
        if method == "tools/call":
            args = (msg.get("params") or {}).get("arguments") or {}
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"echo": args})}
                    ]
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"unknown {method}"},
        }


_FAKE_SCRIPT = r'''
import json, sys

def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("ascii", errors="replace").strip()
        if ":" in text:
            k, v = text.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length") or 0)
    body = sys.stdin.buffer.read(n)
    return json.loads(body.decode("utf-8"))

def write_msg(msg):
    if msg is None:
        return
    raw = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()

TOOLS = [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}]
while True:
    msg = read_msg()
    if msg is None:
        break
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        write_msg({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"fake","version":"0"}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        write_msg({"jsonrpc":"2.0","id":mid,"result":{"tools":TOOLS}})
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        write_msg({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":json.dumps({"echo":args})}]}})
    else:
        write_msg({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":method}})
'''


class McpRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="z_mcp_rt_")
        self.mcp_dir = str(Path(self.tmp) / "mcp")
        self.script = Path(self.tmp) / "fake_mcp.py"
        self.script.write_text(_FAKE_SCRIPT, encoding="utf-8")
        self.env = mock.patch.dict(
            os.environ,
            {
                "Z_HOME": self.tmp,
                "Z_MCP_DIR": self.mcp_dir,
                "Z_MCP_RUNTIME": "1",
            },
        )
        self.env.start()
        # Reset singleton
        import aider.z.mcp_runtime as rt

        rt._MANAGER = None

    def tearDown(self):
        import aider.z.mcp_runtime as rt

        if rt._MANAGER:
            rt._MANAGER.drop_all()
            rt._MANAGER = None
        self.env.stop()

    def _connect_fake(self):
        from aider.z import mcp_local

        # Use custom catalog entry path via connect custom
        return mcp_local.connect(
            "custom",
            credentials={
                "label": "Fake",
                "command": f"{os.sys.executable} {self.script}",
            },
            config={},
        )

    def test_probe_and_call(self):
        from aider.z.mcp_runtime import get_session_manager

        connected = self._connect_fake()
        cid = connected["connection"]["id"]
        # connect already probes; ensure tools cached
        mgr = get_session_manager()
        probe = mgr.probe(cid)
        self.assertTrue(probe["ok"], probe)
        self.assertGreaterEqual(probe.get("tools_count", 0), 1)
        result = mgr.call_tool(cid, "echo", {"hello": "world"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("hello", result.text)

    def test_extract_and_run_fence(self):
        from aider.z import mcp_local
        from aider.z.mcp_turn import extract_mcp_calls, run_mcp_turn

        connected = self._connect_fake()
        server = connected["connection"]["serverName"]
        mcp_local.mark_first_use_confirmed(server, "echo", forever=True)

        text = (
            '```z-mcp\n'
            + json.dumps(
                {"server": server, "tool": "echo", "arguments": {"x": 1}}
            )
            + "\n```"
        )
        calls = extract_mcp_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].tool, "echo")

        res = run_mcp_turn(text, io=None, coder=None)
        self.assertTrue(res.ran)
        self.assertIn("echo", res.reflect_message)

    def test_first_use_blocks_without_confirm(self):
        from aider.z.mcp_turn import run_mcp_turn

        connected = self._connect_fake()
        server = connected["connection"]["serverName"]
        text = (
            '```z-mcp\n'
            + json.dumps({"server": server, "tool": "echo", "arguments": {}})
            + "\n```"
        )
        res = run_mcp_turn(text, io=None)
        self.assertTrue(res.ran)
        self.assertIn("first-use", res.reflect_message.lower())


class UsageHonestyTest(unittest.TestCase):
    def test_unsigned_empty_not_demo(self):
        from aider.z.usage_client import fetch_usage_summary, normalize_for_profile

        with mock.patch.dict(os.environ, {"Z_GATEWAY_USAGE_STUB": ""}, clear=False):
            with mock.patch("aider.z.usage_client.load_credentials", return_value=None):
                raw = fetch_usage_summary("billing_period")
        self.assertFalse(raw["authenticated"])
        self.assertEqual(raw["by_model"], [])
        norm = normalize_for_profile(raw)
        self.assertEqual(norm["totalRequests"], 0)
        self.assertEqual(norm["byModel"], [])


if __name__ == "__main__":
    unittest.main()
