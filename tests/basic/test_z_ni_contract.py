"""Non-interactive run contract: exit codes, outcome line, auto-seed."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from aider.z.ni_contract import (
    NiOutcome,
    apply_ni_reflection_floor,
    auto_seed_chat,
    collect_seed_candidates,
    detect_add_files_miss,
    evaluate_ni_outcome,
    expects_product_edits,
    extract_path_mentions,
    finish_ni_run,
    format_run_outcome,
    is_non_interactive_session,
    maybe_auto_seed_reflect,
    ni_min_reflections,
    ni_require_edits_enabled,
)
from aider.z.task_mode import TaskMode


class DetectAddFilesMissTest(unittest.TestCase):
    def test_please_add_any_of_these(self):
        self.assertTrue(
            detect_add_files_miss(
                "Please add any of these that already exist to the chat."
            )
        )

    def test_please_add_existing_before_execute(self):
        self.assertTrue(
            detect_add_files_miss(
                "Please add these existing files to the chat before I execute "
                "the authorized testing and sanitizer plan:\n"
                "* `CMakeLists.txt`\n"
                "* The current C++ event-bus header and source files\n"
            )
        )

    def test_not_in_chat(self):
        self.assertTrue(
            detect_add_files_miss("Those paths are not in the chat yet. Use /add.")
        )

    def test_search_replace_not_miss(self):
        text = (
            "I'll create the file.\n"
            "src/foo.c\n"
            "<<<<<<< SEARCH\n"
            "=======\n"
            "int main(void) { return 0; }\n"
            ">>>>>>> REPLACE\n"
        )
        self.assertFalse(detect_add_files_miss(text))

    def test_extract_paths(self):
        paths = extract_path_mentions(
            "Create src/foo.c and include/bar.h plus CMakeLists.txt"
        )
        self.assertIn("src/foo.c", paths)
        self.assertIn("include/bar.h", paths)
        self.assertIn("CMakeLists.txt", paths)


class ExpectsEditsTest(unittest.TestCase):
    def test_implement_expects(self):
        self.assertTrue(expects_product_edits(TaskMode.IMPLEMENT, "add a cache"))

    def test_ask_does_not(self):
        self.assertFalse(expects_product_edits(TaskMode.ASK, "what is LRU?"))


class EvaluateNiOutcomeTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("Z_NI_REQUIRE_EDITS", None)

    def tearDown(self):
        os.environ.pop("Z_NI_REQUIRE_EDITS", None)

    def _coder(self, *, edited=None, hold=False, commit=None, reply=""):
        coder = MagicMock()
        coder.aider_edited_files = set(edited or ())
        coder._z_gate_hold_dirty = hold
        coder.last_aider_commit_hash = commit
        coder.last_verification = None
        coder.verify_commit_gate = True
        coder.task_mode = TaskMode.IMPLEMENT
        coder.partial_response_content = reply
        coder.uncertainty_engine = None
        return coder

    def test_zero_edits_implement_exits_nonzero(self):
        coder = self._coder(edited=(), reply="Please add these files…")
        out = evaluate_ni_outcome(
            coder, user_message="Implement src/foo.c", task_mode=TaskMode.IMPLEMENT
        )
        self.assertEqual(out.exit_code, 1)
        self.assertEqual(out.edited_count, 0)
        self.assertIn("no product files edited", out.reason)
        line = format_run_outcome(out)
        self.assertIn("edited=0", line)
        self.assertIn("mode=implement", line)

    def test_edits_ok_exit_zero(self):
        coder = self._coder(edited={"src/foo.c"}, commit="abc1234dead")
        # With commit hash, gate label is ok
        coder.last_verification = MagicMock(state=MagicMock(value="tests_passed"))
        out = evaluate_ni_outcome(
            coder, user_message="Implement src/foo.c", task_mode=TaskMode.IMPLEMENT
        )
        self.assertEqual(out.exit_code, 0)
        self.assertEqual(out.edited_count, 1)

    def test_edits_but_gate_blocked_nonzero(self):
        coder = self._coder(edited={"src/foo.c"}, hold=True)
        out = evaluate_ni_outcome(
            coder, user_message="Implement", task_mode=TaskMode.IMPLEMENT
        )
        self.assertEqual(out.exit_code, 1)
        self.assertEqual(out.gate, "blocked")

    def test_ask_mode_with_reply_ok(self):
        coder = self._coder(edited=(), reply="LRU evicts the least recently used entry.")
        coder.task_mode = TaskMode.ASK
        out = evaluate_ni_outcome(
            coder, user_message="What is LRU?", task_mode=TaskMode.ASK
        )
        self.assertEqual(out.exit_code, 0)

    def test_require_edits_off(self):
        os.environ["Z_NI_REQUIRE_EDITS"] = "0"
        self.assertFalse(ni_require_edits_enabled())
        coder = self._coder(edited=())
        out = evaluate_ni_outcome(
            coder, user_message="Implement", task_mode=TaskMode.IMPLEMENT
        )
        self.assertEqual(out.exit_code, 0)


class AutoSeedTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("Z_NI_AUTO_SEED", None)
        self.root = Path(tempfile.mkdtemp(prefix="z_ni_seed_"))
        (self.root / "src").mkdir()
        (self.root / "src" / "existing.c").write_text("int x;\n", encoding="utf-8")

    def tearDown(self):
        os.environ.pop("Z_NI_AUTO_SEED", None)

    def test_auto_seed_adds_existing_and_reflects(self):
        coder = MagicMock()
        coder.root = str(self.root)
        coder.io = MagicMock()
        coder.io.yes = True
        coder.reflected_message = None
        coder.aider_edited_files = set()
        coder._z_ni_auto_seed_done = False
        coder.get_inchat_relative_files = MagicMock(return_value=[])
        added = []

        def _add(rel):
            added.append(rel)

        coder.add_rel_fname = _add

        ok = maybe_auto_seed_reflect(
            coder,
            user_message="Implement src/existing.c and src/new.c",
            assistant_text="Please add these files to the chat first.",
        )
        self.assertTrue(ok)
        self.assertIn("src/existing.c", added)
        self.assertIsNotNone(coder.reflected_message)
        self.assertIn("SEARCH/REPLACE", coder.reflected_message)
        self.assertIn("src/new.c", coder.reflected_message)

    def test_collect_seed_from_spec_paths(self):
        coder = MagicMock()
        coder.root = str(self.root)
        coder.get_inchat_relative_files = MagicMock(return_value=[])
        cands = collect_seed_candidates(
            coder,
            user_message="Add src/existing.c helper",
            assistant_text="",
        )
        self.assertIn("src/existing.c", cands)

    def test_auto_seed_chat_skips_missing(self):
        coder = MagicMock()
        coder.root = str(self.root)
        added = []
        coder.add_rel_fname = lambda r: added.append(r)
        got = auto_seed_chat(coder, ["src/existing.c", "src/missing.c"])
        self.assertEqual(got, ["src/existing.c"])

    def test_interactive_auto_seed_on_add_files_miss(self):
        """Interactive sessions must not stall on 'please add these files'."""
        from aider.z.ni_contract import discover_topic_files

        (self.root / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
        (self.root / "include").mkdir()
        (self.root / "include" / "event_bus.hpp").write_text("#pragma once\n", encoding="utf-8")
        (self.root / "src" / "event_bus.cpp").write_text("void f(){}\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_event_bus.cpp").write_text("int main(){}\n", encoding="utf-8")

        topics = discover_topic_files(
            self.root,
            "The current C++ event-bus header and source files and test file",
        )
        self.assertTrue(any("event_bus" in t for t in topics), topics)

        coder = MagicMock()
        coder.root = str(self.root)
        coder.io = MagicMock()
        coder.io.yes = None  # interactive — NO --yes-always
        coder.reflected_message = None
        coder.aider_edited_files = set()
        coder._z_ni_auto_seed_done = False
        coder.uncertainty_engine = None
        coder.get_inchat_relative_files = MagicMock(return_value=[])
        added = []
        coder.add_rel_fname = lambda r: added.append(r)

        ok = maybe_auto_seed_reflect(
            coder,
            user_message="run concurrency tests on the event bus",
            assistant_text=(
                "Please add these existing files to the chat before I execute "
                "the authorized testing and sanitizer plan:\n"
                "* `CMakeLists.txt`\n"
                "* The current C++ event-bus header and source files\n"
                "* The current C++ event-bus test file\n"
            ),
        )
        self.assertTrue(ok)
        self.assertIn("CMakeLists.txt", added)
        self.assertTrue(any("event_bus" in a for a in added), added)
        self.assertIn("SEARCH/REPLACE", coder.reflected_message)

    def test_no_yes_flag_needed_after_plan_approve(self):
        """Plan confirm Yes is enough — do not require io.yes / --yes-always."""
        (self.root / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
        eng = MagicMock()
        eng.ctx = MagicMock()
        eng.ctx.plan_approved = True

        coder = MagicMock()
        coder.root = str(self.root)
        coder.io = MagicMock()
        coder.io.yes = None
        coder.reflected_message = None
        coder.aider_edited_files = set()
        coder._z_ni_auto_seed_done = False
        coder.uncertainty_engine = eng
        coder.get_inchat_relative_files = MagicMock(return_value=[])
        added = []
        coder.add_rel_fname = lambda r: added.append(r)

        ok = maybe_auto_seed_reflect(
            coder,
            user_message="test the event bus",
            assistant_text="Please add `CMakeLists.txt` to the chat before I proceed.",
        )
        self.assertTrue(ok)
        self.assertIn("CMakeLists.txt", added)
        self.assertIsNone(coder.io.yes)

    def test_casual_chat_does_not_auto_seed_reflect(self):
        """A plain greeting must never be force-reflected into fabricating edits.

        The casual-chat bypass in base_coder.py sets plan_approved=True purely
        to skip the planning gate for that turn — not as evidence of a stalled
        approved implementation. Without the TaskMode check, that flag alone
        could satisfy the plan_ok branch and fabricate a SEARCH/REPLACE edit
        in response to "hello".
        """
        (self.root / "README.md").write_text("# repo\n", encoding="utf-8")
        eng = MagicMock()
        eng.ctx = MagicMock()
        eng.ctx.plan_approved = True  # set by the casual-chat bypass, not a real plan

        coder = MagicMock()
        coder.root = str(self.root)
        coder.io = MagicMock()
        coder.io.yes = None
        coder.reflected_message = None
        coder.aider_edited_files = set()
        coder._z_ni_auto_seed_done = False
        coder.uncertainty_engine = eng
        coder.task_mode = TaskMode.ASK
        coder.get_inchat_relative_files = MagicMock(return_value=[])
        added = []
        coder.add_rel_fname = lambda r: added.append(r)

        ok = maybe_auto_seed_reflect(
            coder,
            user_message="hello",
            assistant_text=(
                "It seems like you want to initiate a new request. Please let "
                "me know what changes or updates you would like to make."
            ),
        )
        self.assertFalse(ok)
        self.assertEqual(added, [])
        self.assertIsNone(coder.reflected_message)

    def test_non_bare_greeting_does_not_auto_seed_reflect(self):
        """"hey z how u doin" fails looks_like_casual_chat()'s anchored bare-
        greeting regex (extra words after "hey" break it) even though it's
        genuinely ASK-mode chat. Reported bug: this used to still trigger
        auto-seed, adding an unrelated file to chat ("Auto-added to chat —
        aider.chat.history.md") and reflecting the model into fabricating a
        SEARCH/REPLACE edit for a plain greeting. Guarding on the turn's
        already-resolved TaskMode (not just looks_like_casual_chat) closes
        this regardless of exact wording.
        """
        (self.root / "README.md").write_text("# repo\n", encoding="utf-8")
        eng = MagicMock()
        eng.ctx = MagicMock()
        eng.ctx.plan_approved = True

        coder = MagicMock()
        coder.root = str(self.root)
        coder.io = MagicMock()
        coder.io.yes = True  # NI-style session, same as the reported case
        coder.reflected_message = None
        coder.aider_edited_files = set()
        coder._z_ni_auto_seed_done = False
        coder.uncertainty_engine = eng
        coder.task_mode = TaskMode.ASK
        coder.get_inchat_relative_files = MagicMock(return_value=[])
        added = []
        coder.add_rel_fname = lambda r: added.append(r)

        from aider.z.task_mode import looks_like_casual_chat

        self.assertFalse(looks_like_casual_chat("hey z how u doin"))

        ok = maybe_auto_seed_reflect(
            coder,
            user_message="hey z how u doin",
            assistant_text=(
                "I'm doing well, thank you! If you have any requests or "
                "changes you'd like to make, just let me know!"
            ),
        )
        self.assertFalse(ok)
        self.assertEqual(added, [])
        self.assertIsNone(coder.reflected_message)


class ReflectionFloorTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("Z_NI_MIN_REFLECTIONS", None)

    def test_raises_floor_under_yes_always(self):
        coder = MagicMock()
        coder.max_reflections = 3
        coder.io = MagicMock()
        coder.io.yes = True
        apply_ni_reflection_floor(coder)
        self.assertGreaterEqual(coder.max_reflections, ni_min_reflections())

    def test_finish_ni_run_prints_and_returns_code(self):
        io = MagicMock()
        coder = MagicMock()
        coder.aider_edited_files = set()
        coder._z_gate_hold_dirty = False
        coder.last_aider_commit_hash = None
        coder.last_verification = None
        coder.verify_commit_gate = True
        coder.task_mode = TaskMode.IMPLEMENT
        coder.partial_response_content = "Please add files"
        coder.uncertainty_engine = None
        code = finish_ni_run(io, coder, user_message="Implement src/foo.c")
        self.assertEqual(code, 1)
        io.tool_error.assert_called()
        self.assertIn("edited=0", io.tool_error.call_args[0][0])

    def test_is_non_interactive_yes(self):
        io = MagicMock()
        io.yes = True
        self.assertTrue(is_non_interactive_session(io))


class NiOutcomeSummaryTest(unittest.TestCase):
    def test_summary_line(self):
        o = NiOutcome(
            exit_code=1,
            edited_count=0,
            verify="n/a",
            commit="none",
            gate="n/a",
            mode="implement",
            reason="no product files edited",
        )
        self.assertIn("Run outcome:", o.summary_line())


if __name__ == "__main__":
    unittest.main()
