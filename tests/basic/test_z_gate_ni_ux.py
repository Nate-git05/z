"""Gate NI UX: discoverable escapes on commit block + Z_NI_GATE policy."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

_Z_HOME = tempfile.mkdtemp(prefix="z_gate_ni_home_")
os.environ["Z_HOME"] = _Z_HOME
os.environ.pop("Z_FORCE_COMMIT", None)
os.environ.pop("Z_NI_GATE", None)
os.environ["Z_SKIP_VERIFY_GATE"] = ""

from aider.z.uncertainty.gate import (  # noqa: E402
    emit_commit_blocked,
    format_commit_blocked_message,
    ni_gate_policy,
    prepare_commit,
)
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeStatus,
    NodeType,
    Tier,
    UncertaintyNode,
)
from aider.z.uncertainty.store import UncertaintyStore  # noqa: E402
from aider.z.uncertainty.verify import VerificationRecord, VerifyState  # noqa: E402


class FormatCommitBlockedMessageTest(unittest.TestCase):
    def test_contains_escapes_and_dirty_line(self):
        msg = format_commit_blocked_message("high-risk blockers", dirty_count=3)
        self.assertIn("Commit blocked by Z verification gate.", msg)
        self.assertIn("Reason: high-risk blockers", msg)
        self.assertIn("DIRTY (3 files)", msg)
        self.assertIn("Commit did NOT happen", msg)
        self.assertIn("Z_FORCE_COMMIT", msg)
        self.assertIn("Z_SKIP_VERIFY_GATE", msg)
        self.assertIn("Z_NI_GATE", msg)

    def test_emit_prints_via_io(self):
        io = MagicMock()
        msg = emit_commit_blocked(io, "medium-risk not acknowledged", dirty_count=1)
        io.tool_error.assert_called_once()
        printed = io.tool_error.call_args[0][0]
        self.assertEqual(printed, msg)
        self.assertIn("Z_FORCE_COMMIT", printed)
        self.assertIn("DIRTY (1 file)", printed)


class NiGatePolicyTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_NI_GATE", None)
        os.environ.pop("Z_FORCE_COMMIT", None)

    def test_default_block(self):
        os.environ.pop("Z_NI_GATE", None)
        self.assertEqual(ni_gate_policy(), "block")

    def test_force_and_reflect(self):
        os.environ["Z_NI_GATE"] = "force"
        self.assertEqual(ni_gate_policy(), "force")
        os.environ["Z_NI_GATE"] = "REFLECT"
        self.assertEqual(ni_gate_policy(), "reflect")


def _green_record() -> VerificationRecord:
    return VerificationRecord(
        ran=True,
        command="true",
        exit_code=0,
        tests_discovered=1,
        tests_passed=1,
        tests_failed=0,
        zero_tests=False,
        passed=True,
        state=VerifyState.TESTS_PASSED,
    )


def _high_node(title: str = "High stakes auth") -> UncertaintyNode:
    return UncertaintyNode(
        title=title,
        type=NodeType.HIGH_STAKES,
        confidence_tier=Tier.LOW,
        risk_tier=Tier.HIGH,
        summary="Auth path changed without extra verify",
        explanation="test",
        status=NodeStatus.NEEDS_HUMAN_REVIEW,
        files_affected=["a.py"],
    )


class YesAlwaysHighBlockTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("Z_FORCE_COMMIT", None)
        os.environ.pop("Z_NI_GATE", None)
        self.root = Path(tempfile.mkdtemp(prefix="z_gate_ni_"))
        self.store = UncertaintyStore(root=self.root / ".z")

    def tearDown(self):
        os.environ.pop("Z_FORCE_COMMIT", None)
        os.environ.pop("Z_NI_GATE", None)

    def _coder(self, *, yes=True):
        io = MagicMock()
        io.yes = yes
        io.tool_error = MagicMock()
        io.tool_warning = MagicMock()
        io.tool_output = MagicMock()
        io.confirm_ask = MagicMock(return_value=False)

        engine = MagicMock()
        engine.ctx.current_task_id = None
        engine.ctx.current_task_title = None
        engine.ctx.session_id = "t"
        engine.ctx.last_verification = None
        engine.ctx.evidence_ledger = None
        engine.record_execution = MagicMock()
        engine.analyze_after_edits = MagicMock(return_value=[])
        engine.maybe_auto_act = MagicMock(return_value=[])

        coder = MagicMock()
        coder.io = io
        coder.root = str(self.root)
        coder.repo = None
        coder.uncertainty_engine = engine
        coder.uncertainty_store = self.store
        coder.force_commit = False
        coder.verbose = False
        coder.test_cmd = "true"
        coder.aider_edited_files = {"a.py"}
        coder._z_gate_hold_dirty = False
        coder._z_verify_fix_attempts = 0
        coder._z_verify_gen_attempts = 0
        coder.last_verification = None
        return coder, io

    def test_yes_always_high_block_prints_escapes(self):
        coder, io = self._coder(yes=True)
        self.store.add(_high_node())
        self.store.save_local()

        with mock.patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(_green_record(), []),
        ):
            result = prepare_commit(coder, ["a.py"])

        self.assertFalse(result.allow_commit)
        self.assertTrue(result.block_ui_emitted)
        self.assertIn("Z_FORCE_COMMIT", result.block_message or "")
        self.assertIn("Z_SKIP_VERIFY_GATE", result.block_message or "")
        printed = " ".join(
            str(c.args[0]) for c in io.tool_error.call_args_list if c.args
        )
        self.assertIn("Z_FORCE_COMMIT", printed)
        self.assertIn("Z_SKIP_VERIFY_GATE", printed)
        self.assertTrue(io.tool_error.called)

    def test_yes_always_ni_gate_force_allows_commit(self):
        os.environ["Z_NI_GATE"] = "force"
        coder, _io = self._coder(yes=True)
        self.store.add(_high_node())
        self.store.save_local()

        with mock.patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(_green_record(), []),
        ):
            result = prepare_commit(coder, ["a.py"])

        self.assertTrue(result.allow_commit)
        self.assertTrue(result.force_override)

    def test_yes_always_ni_gate_reflect_sets_reflect_message(self):
        os.environ["Z_NI_GATE"] = "reflect"
        coder, _io = self._coder(yes=True)
        self.store.add(_high_node())
        self.store.save_local()

        with mock.patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(_green_record(), []),
        ):
            result = prepare_commit(coder, ["a.py"])

        self.assertFalse(result.allow_commit)
        self.assertIsNotNone(result.reflect_message)
        self.assertIn("Z_NI_GATE=reflect", result.reflect_message)
        self.assertIn("Z_FORCE_COMMIT", result.block_message or "")


if __name__ == "__main__":
    unittest.main()
