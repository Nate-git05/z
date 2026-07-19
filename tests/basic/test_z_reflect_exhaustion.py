"""Reflection-loop exhaustion must raise a High node + commit-blocked message."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

_HOME = tempfile.mkdtemp(prefix="z_reflect_exh_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.engine import SessionContext, UncertaintyEngine  # noqa: E402
from aider.z.uncertainty.gate import (  # noqa: E402
    _effective_gate_tier,
    report_auto_fix_exhaustion,
    resolve_commit_edit_set,
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

    def test_exhausted_lint_pending_with_session_edits_is_loud(self):
        """Lint-fix reflection cap with dirty session edits must not warn-only."""
        record = VerificationRecord(
            ran=False,
            passed=False,
            state=VerifyState.NOT_RUN,
            output_excerpt="",
        )
        coder = self._coder(
            record=record,
            pending="Linter errors:\nfmtlog.cpp: unused variable 'x'\n",
        )
        coder.test_outcome = None
        coder.last_verification = None
        coder.uncertainty_engine.ctx.last_verification = None
        node = report_auto_fix_exhaustion(
            coder, max_reflections=3, pending_reflect=coder.reflected_message
        )
        self.assertIsNotNone(node)
        self.assertTrue(
            any("Commit blocked by Z verification gate" in e for e in coder._errors)
        )


class ResolveCommitEditSetTest(unittest.TestCase):
    def test_current_turn_edits_win(self):
        self.assertEqual(
            resolve_commit_edit_set(["a.cpp"], ["old.cpp"], num_reflections=2),
            {"a.cpp"},
        )

    def test_idle_reflection_reuses_session_edits(self):
        """fmtlog4: lint reflection replied with prose → empty apply_updates."""
        self.assertEqual(
            resolve_commit_edit_set([], ["fmtlog.cpp", "fmtlog.h"], num_reflections=1),
            {"fmtlog.cpp", "fmtlog.h"},
        )

    def test_no_reflection_empty_turn_stays_empty(self):
        self.assertEqual(
            resolve_commit_edit_set([], ["stale.cpp"], num_reflections=0),
            set(),
        )


class DirtyCommitHashTest(unittest.TestCase):
    """dirty_commit must update last_aider_commit_hash like auto_commit does."""

    def test_dirty_commit_records_hash(self):
        from aider.coders.base_coder import Coder

        class FakeIO:
            def tool_output(self, *a, **k):
                pass

            def tool_error(self, *a, **k):
                pass

        class FakeRepo:
            def commit(self, **kwargs):
                return ("deadbeef", "fix: make job queue slot readiness atomic")

        coder = Coder.__new__(Coder)
        coder.io = FakeIO()
        coder.repo = FakeRepo()
        coder.dirty_commits = True
        coder.need_commit_before_edits = {"src/job_queue.hpp"}
        coder.last_aider_commit_hash = None
        coder.aider_commit_hashes = set()
        coder.last_aider_commit_message = None
        coder.show_diffs = False
        coder.commands = MagicMock()

        self.assertTrue(coder.dirty_commit())
        self.assertEqual(coder.last_aider_commit_hash, "deadbeef")
        self.assertIn("deadbeef", coder.aider_commit_hashes)
        self.assertEqual(
            coder.last_aider_commit_message,
            "fix: make job queue slot readiness atomic",
        )

    def test_dirty_commit_noop_without_pending(self):
        from aider.coders.base_coder import Coder

        coder = Coder.__new__(Coder)
        coder.dirty_commits = True
        coder.need_commit_before_edits = set()
        coder.repo = MagicMock()
        coder.last_aider_commit_hash = None
        self.assertIsNone(coder.dirty_commit())
        coder.repo.commit.assert_not_called()
        self.assertIsNone(coder.last_aider_commit_hash)


class ExhaustionSkillCaptureTest(unittest.TestCase):
    """Reflection exhaustion must not forfeit capture of earlier committed work."""

    def _run_one_until_exhaustion(self, *, committed: bool):
        from aider.coders.base_coder import Coder

        suggest_calls = []

        class FakeIO:
            yes = True

            def tool_output(self, *a, **k):
                pass

            def tool_warning(self, *a, **k):
                pass

            def tool_error(self, *a, **k):
                pass

            def confirm_ask(self, *a, **k):
                return False

        coder = Coder.__new__(Coder)
        coder.io = FakeIO()
        coder.verbose = False
        coder.max_reflections = 3
        coder.num_reflections = 0
        coder.reflected_message = None
        coder.aider_edited_files = {"src/ReactFlightDOMClient.js"}
        coder.last_aider_commit_hash = "abc123" if committed else None
        coder.last_verification = None
        coder._z_gate_hold_dirty = False
        coder.root = None

        def fake_init():
            # Preserve session edit/commit state across init_before_message
            coder.num_reflections = 0
            coder.reflected_message = None

        def fake_send(_message):
            coder.reflected_message = "Attempt to fix lint errors?\nunused var"
            if False:
                yield None

        coder.init_before_message = fake_init
        coder.send_message = fake_send
        coder._maybe_pull_skills = lambda *a, **k: None
        coder._maybe_begin_uncertainty_task = lambda *a, **k: None
        coder._maybe_require_implementation_plan = lambda *a, **k: True

        def capture_suggest(msg, **kwargs):
            suggest_calls.append((msg, kwargs))

        coder._maybe_suggest_skill = capture_suggest

        with mock.patch(
            "aider.z.uncertainty.gate.report_auto_fix_exhaustion",
            return_value=None,
        ):
            coder.run_one(
                "Fix encodeFormAction to pass debugValue through the edge client",
                preproc=False,
            )
        return suggest_calls

    def test_exhaustion_with_prior_commit_offers_skill_capture(self):
        calls = self._run_one_until_exhaustion(committed=True)
        self.assertEqual(len(calls), 1)
        msg, kwargs = calls[0]
        self.assertIn("encodeFormAction", msg)
        self.assertTrue(
            kwargs.get("session_scoped_diff"),
            f"exhaustion path must request session-scoped diff, got {kwargs!r}",
        )

    def test_exhaustion_without_commit_skips_skill_capture(self):
        calls = self._run_one_until_exhaustion(committed=False)
        self.assertEqual(calls, [])


class SessionScopedDiffCaptureTest(unittest.TestCase):
    """Exhaustion capture must ground in cumulative session diff, not trailing dirt."""

    def _coder_for_capture(self, *, session_scoped: bool):
        from aider.coders.base_coder import Coder

        class FakeIO:
            yes = True

            def confirm_ask(self, *a, **k):
                return True

            def tool_output(self, *a, **k):
                pass

            def tool_error(self, *a, **k):
                pass

        class FakeRepo:
            def __init__(self):
                self.get_diffs_calls = []
                self.get_diffs_since_calls = []

            def get_diffs(self, fnames=None):
                self.get_diffs_calls.append(list(fnames or []))
                # Uncommitted remainder only — the cosmetic trailing edit
                return (
                    "diff --git a/cache.hpp b/cache.hpp\n"
                    "-size_t n\n"
                    "+std::size_t n\n"
                )

            def get_diffs_since(self, from_commit, fnames=None):
                self.get_diffs_since_calls.append((from_commit, list(fnames or [])))
                # Full session: committed leak fix + trailing cosmetic edit
                return (
                    "diff --git a/cache.hpp b/cache.hpp\n"
                    "-entry->refcount++;\n"
                    "+entry->refcount.fetch_add(1);\n"
                    "-size_t n\n"
                    "+std::size_t n\n"
                )

        coder = Coder.__new__(Coder)
        coder.io = FakeIO()
        coder.verbose = False
        coder.aider_edited_files = {"cache.hpp"}
        coder.last_verification = None
        coder.last_aider_commit_hash = "c0ffee"
        coder._z_gate_hold_dirty = False
        coder.root = None
        coder.commit_before_message = ["sessionstartsha"]
        coder.repo = FakeRepo()
        coder.get_rel_fname = lambda p: str(p)

        captured = {}

        def fake_build_grounding_pack(**kwargs):
            captured["diff"] = kwargs.get("diff", "")
            pack = mock.Mock()
            pack.files = kwargs.get("files_changed") or ["cache.hpp"]
            pack.diff = kwargs.get("diff") or ""
            return pack

        def fake_save_skill_from_task(*args, **kwargs):
            captured["pack"] = kwargs.get("grounding_pack")
            return mock.Mock(slug="test-skill", title="t", path="/tmp/x"), True

        with mock.patch(
            "aider.z.skills.grounding.build_grounding_pack",
            side_effect=fake_build_grounding_pack,
        ), mock.patch(
            "aider.z.skills.cli.save_skill_from_task",
            side_effect=fake_save_skill_from_task,
        ), mock.patch(
            "aider.z.skills.cli.offer_view_new_skill",
            return_value=None,
        ), mock.patch(
            "aider.z.skills.router.task_is_bugfix_intent",
            return_value=True,
        ):
            coder._maybe_suggest_skill(
                "Fix LRU cache memory leak under concurrent eviction",
                session_scoped_diff=session_scoped,
            )
        return coder, captured

    def test_session_scoped_uses_diffs_since_session_start(self):
        coder, captured = self._coder_for_capture(session_scoped=True)
        self.assertEqual(len(coder.repo.get_diffs_since_calls), 1)
        self.assertEqual(coder.repo.get_diffs_since_calls[0][0], "sessionstartsha")
        self.assertEqual(coder.repo.get_diffs_calls, [])
        self.assertIn("refcount.fetch_add", captured["diff"])
        self.assertIn("std::size_t", captured["diff"])

    def test_clean_exit_keeps_uncommitted_only_diff(self):
        coder, captured = self._coder_for_capture(session_scoped=False)
        self.assertEqual(len(coder.repo.get_diffs_calls), 1)
        self.assertEqual(coder.repo.get_diffs_since_calls, [])
        self.assertIn("std::size_t", captured["diff"])
        self.assertNotIn("refcount.fetch_add", captured["diff"])


if __name__ == "__main__":
    unittest.main()
