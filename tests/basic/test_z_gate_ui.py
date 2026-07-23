"""Commit-gate visualization panel — pure rendering of a GateResult."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aider.z.uncertainty.gate import GateResult
from aider.z.uncertainty.schema import NodeStatus, NodeType, Tier, UncertaintyNode


def _node(title: str, *, risk: Tier = Tier.HIGH) -> UncertaintyNode:
    return UncertaintyNode(
        title=title,
        type=NodeType.MISSING_TEST,
        confidence_tier=Tier.LOW,
        risk_tier=risk,
        summary=f"summary for {title}",
        status=NodeStatus.OPEN,
    )


def _fake_io(*, pretty: bool = True):
    return SimpleNamespace(
        console=MagicMock(),
        pretty=pretty,
        tool_output=MagicMock(),
    )


class RenderCommitGateBlockedTests(unittest.TestCase):
    def test_blocked_high_pretty_shows_panel_with_node_titles(self):
        from aider.z.uncertainty.gate_ui import render_commit_gate

        result = GateResult(
            allow_commit=False,
            blocked_high=[_node("Untested path")],
            needs_ack_medium=[_node("Config assumption", risk=Tier.MEDIUM)],
            reason="high-risk blockers",
        )
        io = _fake_io(pretty=True)
        render_commit_gate(result, io=io, dirty_count=1)

        io.console.print.assert_called_once()
        panel = io.console.print.call_args[0][0]
        rendered = str(panel.renderable)
        self.assertIn("Untested path", rendered)
        self.assertIn("Config assumption", rendered)
        self.assertIn("Z_FORCE_COMMIT", rendered)

    def test_blocked_non_pretty_uses_tool_output_and_includes_escape_hatch(self):
        from aider.z.uncertainty.gate_ui import render_commit_gate

        result = GateResult(
            allow_commit=False,
            blocked_high=[_node("Untested path")],
            reason="high-risk blockers",
        )
        io = _fake_io(pretty=False)
        render_commit_gate(result, io=io, dirty_count=2)

        io.console.print.assert_not_called()
        lines = "\n".join(c.args[0] for c in io.tool_output.call_args_list)
        self.assertIn("Untested path", lines)
        self.assertIn("Z_FORCE_COMMIT", lines)
        self.assertIn("Z_SKIP_VERIFY_GATE", lines)

    def test_block_ui_emitted_reuses_existing_block_message(self):
        """When gate.py already computed a specific block_message, the panel
        must reuse it verbatim instead of recomputing a generic one."""
        from aider.z.uncertainty.gate_ui import render_commit_gate

        result = GateResult(
            allow_commit=False,
            blocked_high=[_node("Untested path")],
            reason="high-risk blockers",
            block_ui_emitted=True,
            block_message="A very specific pre-rendered block message",
        )
        io = _fake_io(pretty=True)
        render_commit_gate(result, io=io, dirty_count=1)
        panel = io.console.print.call_args[0][0]
        self.assertIn("A very specific pre-rendered block message", str(panel.renderable))


class RenderCommitGateClearTests(unittest.TestCase):
    def test_clear_case_pretty_looks_distinct_from_blocked(self):
        from aider.z.uncertainty.gate_ui import render_commit_gate

        result = GateResult(allow_commit=True)
        io = _fake_io(pretty=True)
        render_commit_gate(result, io=io)

        io.console.print.assert_called_once()
        panel = io.console.print.call_args[0][0]
        text = str(panel.renderable)
        self.assertIn("proceeding", str(panel.subtitle))
        self.assertNotIn("Z_FORCE_COMMIT", text)

    def test_clear_case_surfaces_acknowledged_medium(self):
        """Acknowledged-medium nodes are otherwise invisible today — the
        clear panel's whole value-add is surfacing them."""
        from aider.z.uncertainty.gate_ui import render_commit_gate

        result = GateResult(
            allow_commit=True,
            acknowledged_medium=[_node("Config assumption", risk=Tier.MEDIUM)],
        )
        io = _fake_io(pretty=True)
        render_commit_gate(result, io=io)
        panel = io.console.print.call_args[0][0]
        self.assertIn("Config assumption", str(panel.renderable))

    def test_clear_non_pretty_single_line(self):
        from aider.z.uncertainty.gate_ui import render_commit_gate

        result = GateResult(allow_commit=True)
        io = _fake_io(pretty=False)
        render_commit_gate(result, io=io)
        io.console.print.assert_not_called()
        io.tool_output.assert_called()
        self.assertIn("clear", io.tool_output.call_args_list[0].args[0].lower())


class NoSideEffectRegressionTests(unittest.TestCase):
    """render_commit_gate must be pure rendering — never duplicate gate.py's
    own ledger/IPC side effects (those live in emit_commit_blocked)."""

    def test_never_touches_ledger_or_ipc(self):
        from aider.z.uncertainty.gate_ui import render_commit_gate

        result = GateResult(
            allow_commit=False,
            blocked_high=[_node("Untested path")],
            reason="high-risk blockers",
        )
        io = _fake_io(pretty=True)
        io._notify = MagicMock()
        with patch(
            "aider.z.uncertainty.commit_block_ledger.append_block"
        ) as append_block, patch(
            "aider.z.uncertainty.gate.emit_commit_blocked"
        ) as emit_blocked:
            render_commit_gate(result, io=io, dirty_count=1)
        append_block.assert_not_called()
        emit_blocked.assert_not_called()
        io._notify.assert_not_called()


class AutoCommitBlockedRendersSamePanelTest(unittest.TestCase):
    """The agent's own auto-commit block path must render the identical
    panel manual /commit uses — this was the biggest inconsistency found in
    the UX audit (same event, two different treatments)."""

    def test_renders_panel_and_records_outcome(self):
        from aider.coders.base_coder import Coder

        coder = Coder.__new__(Coder)
        coder.io = _fake_io(pretty=True)
        coder.move_back_cur_messages = MagicMock()
        coder._report_gateway_routing_outcome = MagicMock()
        coder._gateway_escalation_depth = 0

        result = GateResult(
            allow_commit=False,
            blocked_high=[_node("Untested path")],
            reason="high-risk blockers",
        )
        coder._handle_verify_gate_blocked(result, ["file.py"])

        coder.io.console.print.assert_called_once()
        panel = coder.io.console.print.call_args[0][0]
        self.assertIn("Untested path", str(panel.renderable))
        coder.move_back_cur_messages.assert_called_once()
        coder._report_gateway_routing_outcome.assert_called_once_with(False, result)
        self.assertEqual(coder._gateway_escalation_depth, 1)

    def test_reuses_block_message_when_gate_already_emitted_one(self):
        from aider.coders.base_coder import Coder

        coder = Coder.__new__(Coder)
        coder.io = _fake_io(pretty=True)
        coder.move_back_cur_messages = MagicMock()
        coder._report_gateway_routing_outcome = MagicMock()
        coder._gateway_escalation_depth = 0

        result = GateResult(
            allow_commit=False,
            blocked_high=[_node("Untested path")],
            reason="high-risk blockers",
            block_ui_emitted=True,
            block_message="a very specific pre-rendered message",
        )
        coder._handle_verify_gate_blocked(result, ["file.py"])

        coder.move_back_cur_messages.assert_called_once_with(
            "a very specific pre-rendered message"
        )


if __name__ == "__main__":
    unittest.main()
