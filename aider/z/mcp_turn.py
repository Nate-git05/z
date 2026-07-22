"""
MCP turn fence protocol (Phase 11 path B).

The model may request MCP tools via::

    ```z-mcp
    {"server":"github","tool":"list_issues","arguments":{"owner":"o","repo":"r"}}
    ```

Or a single-line form::

    <<<Z_MCP_CALL
    {"server":"github","tool":"list_issues","arguments":{}}
    >>>

Z runs the call (with first-use gate), budgets output, and reflects.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from aider.z.mcp_client import format_mcp_result_for_chat, note_unverifiable_mcp_result
from aider.z.mcp_runtime import get_session_manager, runtime_enabled


_FENCE_RE = re.compile(
    r"```z-mcp\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_ANGLE_RE = re.compile(
    r"<<<Z_MCP_CALL\s*\n(.*?)\n>>>",
    re.DOTALL | re.IGNORECASE,
)

NotifyFn = Callable[[str, dict], None]


@dataclass
class McpCall:
    server: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpTurnResult:
    calls: List[McpCall] = field(default_factory=list)
    reflect_message: str = ""
    ran: bool = False


def mcp_turn_enabled() -> bool:
    if not runtime_enabled():
        return False
    raw = os.environ.get("Z_MCP_TURN", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def mcp_turn_max() -> int:
    raw = os.environ.get("Z_MCP_TURN_MAX", "").strip()
    if raw.isdigit():
        return max(1, min(6, int(raw)))
    return 3


def extract_mcp_calls(text: str) -> List[McpCall]:
    if not text:
        return []
    bodies: list[str] = []
    for m in _FENCE_RE.finditer(text):
        bodies.append(m.group(1) or "")
    for m in _ANGLE_RE.finditer(text):
        bodies.append(m.group(1) or "")
    calls: list[McpCall] = []
    for body in bodies:
        body = body.strip()
        if not body:
            continue
        # Allow multiple JSON objects (one per line or concatenated)
        chunks = _split_json_chunks(body)
        for chunk in chunks:
            try:
                data = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            server = str(
                data.get("server") or data.get("serverName") or data.get("server_name") or ""
            ).strip()
            tool = str(
                data.get("tool") or data.get("toolName") or data.get("tool_name") or ""
            ).strip()
            args = data.get("arguments") or data.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            if tool:
                calls.append(McpCall(server=server, tool=tool, arguments=args))
    return calls


def _split_json_chunks(body: str) -> list[str]:
    body = body.strip()
    if body.startswith("["):
        try:
            arr = json.loads(body)
            if isinstance(arr, list):
                return [json.dumps(x) for x in arr if isinstance(x, dict)]
        except json.JSONDecodeError:
            pass
    if body.startswith("{"):
        # Try whole body first
        try:
            json.loads(body)
            return [body]
        except json.JSONDecodeError:
            pass
        # Line-delimited
        out: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("{"):
                out.append(line)
        return out or [body]
    return [body]


def format_mcp_catalog_reminder(tool_index: list[dict[str, Any]]) -> str:
    if not tool_index:
        return ""
    lines = [
        "# MCP tools (Z)",
        "Connected MCP tools are available. To call one, emit a ```z-mcp fence with JSON:",
        '  {"server":"<serverName>","tool":"<toolName>","arguments":{...}}',
        "Available:",
    ]
    for row in tool_index[:40]:
        server = row.get("serverName") or "?"
        tool = row.get("toolName") or "?"
        desc = (row.get("description") or "").replace("\n", " ")[:80]
        suffix = f" — {desc}" if desc else ""
        lines.append(f"- {server}.{tool}{suffix}")
    if len(tool_index) > 40:
        lines.append(f"… and {len(tool_index) - 40} more")
    return "\n".join(lines) + "\n"


def run_mcp_turn(
    text: str,
    *,
    io=None,
    coder=None,
    notify: Optional[NotifyFn] = None,
    turn_id: Optional[str] = None,
) -> McpTurnResult:
    """
    Parse MCP fences from model text, execute up to ``mcp_turn_max`` calls,
    return a reflect message for the next model step.
    """
    result = McpTurnResult()
    if not mcp_turn_enabled() or not text:
        return result
    calls = extract_mcp_calls(text)
    if not calls:
        return result
    calls = calls[: mcp_turn_max()]
    result.calls = calls
    mgr = get_session_manager()
    blocks: list[str] = []

    for call in calls:
        resolved = mgr.resolve_tool(call.server, call.tool)
        if resolved is None:
            blocks.append(
                f"### MCP `{call.server or '?'}`.`{call.tool}`\n"
                f"error: no connected MCP server provides this tool "
                f"(connect it in the MCP panel)."
            )
            continue
        connection_id, server_name = resolved

        # D9 first-use
        allowed = True
        if io is not None and hasattr(io, "confirm_mcp_first_use"):
            try:
                allowed = bool(io.confirm_mcp_first_use(server_name, call.tool))
            except Exception:
                allowed = False
        else:
            from aider.z import mcp_local

            if mcp_local.needs_first_use_confirm(server_name, call.tool):
                # CLI without interactive confirm: require prior panel trust
                blocks.append(
                    f"### MCP `{server_name}`.`{call.tool}`\n"
                    f"error: first-use not confirmed. "
                    f"Trust this server in the MCP panel (or reply yes in Chat)."
                )
                continue

        if not allowed:
            blocks.append(
                f"### MCP `{server_name}`.`{call.tool}`\n"
                f"error: user denied first-use for this MCP tool."
            )
            continue

        call_id = ""
        if notify:
            notify(
                "mcp/tool_started",
                {
                    "turnId": turn_id,
                    "serverName": server_name,
                    "toolName": call.tool,
                    "callId": "",
                },
            )

        t0_result = mgr.call_tool(
            connection_id, call.tool, call.arguments, timeout=60.0
        )
        call_id = t0_result.call_id
        if notify:
            # patch started with id if we emitted empty
            notify(
                "mcp/tool_started",
                {
                    "turnId": turn_id,
                    "serverName": server_name,
                    "toolName": call.tool,
                    "callId": call_id,
                },
            )

        if t0_result.ok:
            budgeted = format_mcp_result_for_chat(call.tool, t0_result.text)
            blocks.append(
                f"### MCP `{server_name}`.`{call.tool}` "
                f"({t0_result.duration_ms}ms)\n{budgeted}"
            )
            note_unverifiable_mcp_result(call.tool, coder=coder)
            if notify:
                notify(
                    "mcp/tool_finished",
                    {
                        "turnId": turn_id,
                        "callId": call_id,
                        "serverName": server_name,
                        "toolName": call.tool,
                        "ok": True,
                        "summary": (t0_result.text or "")[:160],
                        "durationMs": t0_result.duration_ms,
                    },
                )
            if io is not None:
                try:
                    io.tool_output(
                        f"MCP: {server_name}.{call.tool} ok ({t0_result.duration_ms}ms)"
                    )
                except Exception:
                    pass
        else:
            err = t0_result.error or "tool call failed"
            blocks.append(f"### MCP `{server_name}`.`{call.tool}`\nerror: {err}")
            if notify:
                notify(
                    "mcp/tool_error",
                    {
                        "turnId": turn_id,
                        "callId": call_id,
                        "serverName": server_name,
                        "toolName": call.tool,
                        "error": err,
                    },
                )
            if io is not None:
                try:
                    io.tool_warning(f"MCP: {server_name}.{call.tool} failed: {err}")
                except Exception:
                    pass

    if not blocks:
        return result
    result.ran = True
    result.reflect_message = (
        "MCP tool results (use these facts; do not invent tool output):\n\n"
        + "\n\n".join(blocks)
        + "\n\nContinue the task with this information."
    )
    return result
