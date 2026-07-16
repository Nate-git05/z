"""Reflection-loop exhaustion must raise a High node + commit-blocked message."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_HOME = tempfile.mkdtemp(prefix="z_reflect_exh_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.engine import SessionContext, UncertaintyEngine  # noqa: E402
from aider.z.uncertainty.gate import (  # noqa: E402
    _effective_gate_tier,
    report_auto_fix_exhaustion,
)
from aider.z.uncertainty.schema import NodeType, Tier  # noqa: E402
from aider.z.uncertainty.store import UncertaintyStore  # noqa: E402
from aider.z.uncertainty.verify import VerificationRecord, VerifyState  # noqa: E402


class AutoFixExhaustionTest(unittest.TestCase):
    def _coder(self, *, record: VerificationRecord, pending: str = ""):
        root = Path(tempfile.mkdtemp(prefix="z_exh_repo_"))
        store = UncertaintyStore(root=root, repo_key=str(root))
        eng = UncertaintyEngine(SessionContext(root=root, store=store))
        eng.ctx.last_verification = record

        errors = []
        warnings = []
        io = MagicMock()
        io.tool_error = errors.append
        io.tool_warning = warnings.append

        coder = MagicMock()
        coder.io = io
        coder.uncertainty_engine = eng
        coder.uncertainty_store = store
        coder.last_verification = record
        coder.test_outcome = False
        coder.reflected_message = pending
        coder.aider_edited_files = {str(root / "logveil" / "redact.py")}
        coder.move_back_cur_messages = MagicMock()
        coder._errors = errors
        coder._warnings = warnings
        return coder

    def test_exhausted_failing_tests_raises_high_node(self):
        record = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=1,
            tests_discovered=12,
            tests_passed=11,
            tests_failed=1,
            passed=False,
            state=VerifyState.TESTS_FAILED,
            output_excerpt=(
                "FAILED tests/test_ipv4.py::test_rejects_invalid_octet\n"
                "AssertionError: '256.0.0.1' unexpectedly redacted as "
                "'2[REDACTED].0.0.1'\n"
            ),
        )
        coder = self._coder(
            record=record,
            pending="Z verification gate: the test suite failed after your edits",
        )
        node = report_auto_fix_exhaustion(
            coder, max_reflections=3, pending_reflect=coder.reflected_message
        )
        self.assertIsNotNone(node)
        self.assertEqual(node.type, NodeType.MISSING_TEST)
        self.assertTrue(node.signals.get("auto_fix_exhausted"))
        self.assertTrue(node.signals.get("verification_blocked"))
        self.assertEqual(_effective_gate_tier(node), Tier.HIGH)
        self.assertIn("Auto-fix exhausted", node.title)
        self.assertIn("256.0.0.1", node.explanation)
        self.assertTrue(any("Commit blocked by Z verification gate" in e for e in coder._errors))
        self.assertTrue(any("auto-fix exhausted" in e.lower() for e in coder._errors))
        coder.move_back_cur_messages.assert_called()

    def test_exhausted_without_failing_tests_keeps_warning_only(self):
        record = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=0,
            tests_discovered=5,
            tests_passed=5,
            tests_failed=0,
            passed=True,
            state=VerifyState.TESTS_PASSED,
            output_excerpt="5 passed",
        )
        coder = self._coder(record=record, pending="Please add these files to the chat")
        coder.test_outcome = True
        node = report_auto_fix_exhaustion(
            coder, max_reflections=3, pending_reflect=coder.reflected_message
        )
        self.assertIsNone(node)
        self.assertTrue(
            any("reflections allowed" in w.lower() for w in coder._warnings)
        )
        self.assertFalse(
            any("Commit blocked by Z verification gate" in e for e in coder._errors)
        )


if __name__ == "__main__":
    unittest.main()
