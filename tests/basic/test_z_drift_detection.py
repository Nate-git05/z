"""Drift/fixation detection during reflection — off-scope + checklist stagnation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

_HOME = tempfile.mkdtemp(prefix="z_drift_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.drift import (  # noqa: E402
    ReflectionTurn,
    checklist_progressed,
    confirm_prompt,
    detect_drift,
    file_in_checklist_scope,
    format_refocus_message,
    is_complete_task_creep,
    make_drift_observed_node,
    off_scope_edits,
    status_snapshot,
)
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
)
from aider.z.uncertainty.evidence_strategy import (  # noqa: E402
    STATUS_FULLY,
    STATUS_NOT,
    STATUS_PARTIAL,
)


def _react_checklist() -> TaskChecklist:
    return TaskChecklist(
        task_id="t1",
        title="encodeFormAction",
        items=[
            RequirementItem(
                id="i1",
                text=(
                    "Update encodeFormAction in "
                    "packages/react-server-dom-webpack/src/client/ReactFlightDOMClient.js "
                    "and packages/react-server-dom-turbopack/src/client/ReactFlightDOMClient.js "
                    "to pass debugValue through the edge client"
                ),
                status=STATUS_NOT,
                kind="product",
            ),
            RequirementItem(
                id="i2",
                text="Add a regression test for encodeFormAction",
                status=STATUS_NOT,
                kind="verification",
            ),
        ],
    )


class ScopeMatchingTest(unittest.TestCase):
    def test_product_paths_are_in_scope(self):
        cl = _react_checklist()
        off = off_scope_edits(
            [
                "packages/react-server-dom-webpack/src/client/ReactFlightDOMClient.js",
            ],
            cl,
        )
        self.assertEqual(off, [])

    def test_unrelated_file_is_off_scope(self):
        cl = _react_checklist()
        off = off_scope_edits(
            [
                "packages/react-devtools-shared/src/bridge.js",
                "src/lru_cache.hpp",
            ],
            cl,
        )
        self.assertIn("packages/react-devtools-shared/src/bridge.js", off)
        self.assertIn("src/lru_cache.hpp", off)

    def test_empty_scope_checklist_cannot_judge_off_scope(self):
        cl = TaskChecklist(
            task_id="t",
            title="vague",
            items=[
                RequirementItem(
                    id="a",
                    text="Make it faster somehow",
                    status=STATUS_NOT,
                )
            ],
        )
        self.assertEqual(off_scope_edits(["src/foo.cpp"], cl), [])

    def test_investigation_symbol_counts_as_scope(self):
        cl = TaskChecklist(
            task_id="t",
            title="race",
            items=[
                RequirementItem(
                    id="a",
                    text="Also check bgLogInfos while registerLogInfo grows",
                    status=STATUS_NOT,
                    kind="investigation",
                )
            ],
        )
        self.assertTrue(
            file_in_checklist_scope(
                "fmtlog/bgLogInfos.cpp",
                [],
                ["bgLogInfos", "registerLogInfo"],
            )
        )
        off = off_scope_edits(["fmtlog/bgLogInfos.cpp"], cl)
        self.assertEqual(off, [])

    def test_resolved_file_no_longer_anchors_scope(self):
        """LRU live miss: after lru_cache.h item is Fully Addressed, further
        edits to that same file must count as off-scope (refactor creep)."""
        from aider.z.uncertainty.drift import checklist_scope, detect_drift

        cl = TaskChecklist(
            task_id="lru",
            title="Fix LRU leak",
            items=[
                RequirementItem(
                    id="fix",
                    text="Fix memory leak in src/lru_cache.h under concurrent eviction",
                    status=STATUS_FULLY,
                    kind="product",
                ),
                RequirementItem(
                    id="verify",
                    text="Confirm with LeakSanitizer before/after",
                    status=STATUS_NOT,
                    kind="verification",
                ),
            ],
        )
        paths, symbols = checklist_scope(cl)
        self.assertNotIn("src/lru_cache.h", paths)
        # Still editing the resolved file → off-scope
        off = off_scope_edits(["src/lru_cache.h"], cl)
        self.assertEqual(off, ["src/lru_cache.h"])
        # And the stagnation window can fire on that file
        history = [
            ReflectionTurn(
                files={"src/lru_cache.h"},
                progressed=False,
                off_scope=["src/lru_cache.h"],
            ),
            ReflectionTurn(
                files={"src/lru_cache.h"},
                progressed=False,
                off_scope=["src/lru_cache.h"],
            ),
        ]
        signal = detect_drift(history, cl)
        self.assertIsNotNone(signal)
        self.assertIn("lru_cache.h", ",".join(signal.off_scope_files))

    def test_regression_reopens_file_scope(self):
        """If rescoring drops Fully → Partial, the file is in-scope again."""
        from aider.z.uncertainty.drift import checklist_scope

        cl = TaskChecklist(
            task_id="lru",
            title="Fix LRU leak",
            items=[
                RequirementItem(
                    id="fix",
                    text="Fix memory leak in src/lru_cache.h",
                    status=STATUS_FULLY,
                    kind="product",
                ),
            ],
        )
        self.assertEqual(checklist_scope(cl)[0], [])
        cl.items[0].status = STATUS_PARTIAL
        paths, _ = checklist_scope(cl)
        self.assertIn("src/lru_cache.h", paths)
        self.assertEqual(off_scope_edits(["src/lru_cache.h"], cl), [])


class ProgressAndDetectTest(unittest.TestCase):
    def test_progressed_when_status_improves(self):
        cl = _react_checklist()
        before = status_snapshot(cl)
        cl.items[0].status = STATUS_PARTIAL
        self.assertTrue(checklist_progressed(before, cl))

    def test_no_drift_when_checklist_progresses(self):
        cl = _react_checklist()
        history = [
            ReflectionTurn(
                files={"packages/react-devtools-shared/src/bridge.js"},
                progressed=True,
                off_scope=["packages/react-devtools-shared/src/bridge.js"],
            ),
            ReflectionTurn(
                files={"packages/react-devtools-shared/src/bridge.js"},
                progressed=False,
                off_scope=["packages/react-devtools-shared/src/bridge.js"],
            ),
        ]
        self.assertIsNone(detect_drift(history, cl))

    def test_no_drift_when_edits_in_scope(self):
        cl = _react_checklist()
        in_scope = (
            "packages/react-server-dom-webpack/src/client/ReactFlightDOMClient.js"
        )
        history = [
            ReflectionTurn(files={in_scope}, progressed=False, off_scope=[]),
            ReflectionTurn(files={in_scope}, progressed=False, off_scope=[]),
        ]
        self.assertIsNone(detect_drift(history, cl))

    def test_flags_two_off_scope_stagnant_reflections(self):
        cl = _react_checklist()
        history = [
            ReflectionTurn(
                files={"packages/react-devtools-shared/src/bridge.js"},
                progressed=False,
                off_scope=["packages/react-devtools-shared/src/bridge.js"],
            ),
            ReflectionTurn(
                files={"src/debugValue.ts"},
                progressed=False,
                off_scope=["src/debugValue.ts"],
            ),
        ]
        signal = detect_drift(history, cl)
        self.assertIsNotNone(signal)
        self.assertIn("bridge.js", ",".join(signal.off_scope_files))
        self.assertTrue(signal.unresolved)
        self.assertIn("Possible drift", signal.summary)

    def test_mixed_in_and_off_scope_does_not_flag(self):
        """Legitimate large-but-in-scope work plus a stray file must not trip."""
        cl = _react_checklist()
        in_scope = (
            "packages/react-server-dom-webpack/src/client/ReactFlightDOMClient.js"
        )
        history = [
            ReflectionTurn(
                files={in_scope, "tmp/scratch.js"},
                progressed=False,
                off_scope=["tmp/scratch.js"],
            ),
            ReflectionTurn(
                files={in_scope},
                progressed=False,
                off_scope=[],
            ),
        ]
        self.assertIsNone(detect_drift(history, cl))


class ConfirmAndNodeTest(unittest.TestCase):
    def test_refocus_message_lists_open_items(self):
        cl = _react_checklist()
        history = [
            ReflectionTurn(
                files={"a.js"}, progressed=False, off_scope=["a.js"]
            ),
            ReflectionTurn(
                files={"b.js"}, progressed=False, off_scope=["b.js"]
            ),
        ]
        signal = detect_drift(history, cl)
        msg = format_refocus_message(signal)
        self.assertIn("encodeFormAction", msg)
        self.assertIn("refocus", msg.lower())
        prompt = confirm_prompt(signal)
        self.assertIn("Refocus on the original task instead?", prompt)

    def test_complete_task_creep_still_flags(self):
        """After every item is Fully Addressed, same-file creep must still fire."""
        cl = TaskChecklist(
            task_id="lru",
            title="Fix LRU leak",
            items=[
                RequirementItem(
                    id="fix",
                    text="Fix memory leak in src/lru_cache.h",
                    status=STATUS_FULLY,
                    kind="product",
                ),
            ],
        )
        history = [
            ReflectionTurn(
                files={"src/lru_cache.h"},
                progressed=False,
                off_scope=["src/lru_cache.h"],
            ),
            ReflectionTurn(
                files={"src/lru_cache.h"},
                progressed=False,
                off_scope=["src/lru_cache.h"],
            ),
        ]
        signal = detect_drift(history, cl)
        self.assertIsNotNone(signal)
        self.assertTrue(is_complete_task_creep(signal))
        self.assertEqual(signal.unresolved, [])
        self.assertIn("already resolved", signal.summary)
        self.assertIsNone(format_refocus_message(signal))
        prompt = confirm_prompt(signal)
        self.assertIn("Stop here?", prompt)
        self.assertNotIn("Refocus on the original task", prompt)
        node = make_drift_observed_node(signal, task_id="lru")
        self.assertTrue(node.signals.get("complete_task_creep"))

    def test_declined_node_is_medium_requirement_gap(self):
        cl = _react_checklist()
        history = [
            ReflectionTurn(
                files={"a.js"}, progressed=False, off_scope=["a.js"]
            ),
            ReflectionTurn(
                files={"b.js"}, progressed=False, off_scope=["b.js"]
            ),
        ]
        signal = detect_drift(history, cl)
        node = make_drift_observed_node(signal, task_id="t1")
        self.assertEqual(node.type, NodeType.REQUIREMENT_GAP)
        self.assertEqual(node.risk_tier, Tier.MEDIUM)
        self.assertTrue(node.signals.get("drift_observed"))
        self.assertIn("Drift observed", node.title)


class DriftGateInRunOneTest(unittest.TestCase):
    """Integration: confirm gate redirects reflected_message at most once."""

    def _run_drifting_loop(self, *, accept_refocus: bool):
        from aider.coders.base_coder import Coder
        from aider.z.uncertainty.engine import SessionContext, UncertaintyEngine
        from aider.z.uncertainty.store import UncertaintyStore

        root = Path(tempfile.mkdtemp(prefix="z_drift_repo_"))
        store = UncertaintyStore(root=root, repo_key=str(root))
        eng = UncertaintyEngine(SessionContext(root=root, store=store))
        eng.ctx.checklist = _react_checklist()

        confirms = []
        warnings = []

        class FakeIO:
            yes = True  # --yes-always must NOT auto-accept (explicit_yes_required)

            def tool_output(self, *a, **k):
                pass

            def tool_warning(self, *a, **k):
                warnings.append(a[0] if a else "")

            def tool_error(self, *a, **k):
                pass

            def confirm_ask(self, question, default="y", explicit_yes_required=False, **k):
                confirms.append(
                    {
                        "q": question,
                        "default": default,
                        "explicit": explicit_yes_required,
                    }
                )
                if explicit_yes_required and self.yes is True and not accept_refocus:
                    # Mirror real io.py: yes-always + explicit → default no
                    return False
                return accept_refocus

        coder = Coder.__new__(Coder)
        coder.io = FakeIO()
        coder.verbose = False
        coder.max_reflections = 3
        coder.num_reflections = 0
        coder.reflected_message = None
        coder.aider_edited_files = set()
        coder.last_aider_commit_hash = None
        coder.last_verification = None
        coder._z_gate_hold_dirty = False
        coder.root = root
        coder.uncertainty_engine = eng
        coder.uncertainty_store = store
        coder._drift_asked_this_task = False
        coder._drift_reflection_log = []

        send_n = {"n": 0}
        sent_messages = []

        def fake_init():
            coder.num_reflections = 0
            coder.reflected_message = None
            coder._drift_reflection_log = []
            # Keep aider_edited_files across the fake init so deltas work;
            # real init clears them — reset explicitly for the test turn.
            coder.aider_edited_files = set()

        def fake_send(message):
            sent_messages.append(message)
            send_n["n"] += 1
            # 1: original turn (in-scope)
            # 2–3: off-scope reflection turns → drift window
            # 4+: stop (or consume refocus redirect)
            if send_n["n"] == 1:
                edited = {
                    str(
                        root
                        / "packages/react-server-dom-webpack/src/client/"
                        / "ReactFlightDOMClient.js"
                    )
                }
                coder._last_send_edited_files = set(edited)
                coder.aider_edited_files.update(edited)
                coder.reflected_message = "Attempt to fix lint errors?\nunused"
            elif send_n["n"] == 2:
                edited = {
                    str(root / "packages/react-devtools-shared/src/bridge.js")
                }
                coder._last_send_edited_files = set(edited)
                coder.aider_edited_files.update(edited)
                coder.reflected_message = "Attempt to fix lint errors?\nstill unused"
            elif send_n["n"] == 3:
                # Clearly off-scope (not a checklist product path / strong target)
                edited = {str(root / "src/lru_cache.hpp")}
                coder._last_send_edited_files = set(edited)
                coder.aider_edited_files.update(edited)
                # Keep reflecting so the n→3 checkpoint can run detect_drift
                coder.reflected_message = "Attempt to fix lint errors?\nagain"
            else:
                coder._last_send_edited_files = set()
                coder.reflected_message = None
            if False:
                yield None

        coder.init_before_message = fake_init
        coder.send_message = fake_send
        coder._maybe_pull_skills = lambda *a, **k: None
        coder._maybe_begin_uncertainty_task = lambda *a, **k: None
        coder._maybe_require_implementation_plan = lambda *a, **k: True
        coder._maybe_suggest_skill = lambda *a, **k: None
        coder.get_rel_fname = lambda p: os.path.relpath(str(p), str(root))

        with mock.patch(
            "aider.z.uncertainty.gate.report_auto_fix_exhaustion",
            return_value=None,
        ):
            coder.run_one(
                "Fix encodeFormAction to pass debugValue through the edge client",
                preproc=False,
            )
        return confirms, warnings, sent_messages, store

    def test_yes_always_does_not_auto_refocus(self):
        confirms, warnings, sent, store = self._run_drifting_loop(accept_refocus=False)
        self.assertTrue(confirms)
        self.assertTrue(confirms[0]["explicit"])
        self.assertEqual(confirms[0]["default"], "n")
        # Exactly one ask per task
        self.assertEqual(len(confirms), 1)
        nodes = [n for n in store.list() if n.signals.get("drift_observed")]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].risk_tier, Tier.MEDIUM)
        self.assertTrue(any("Drift observed" in w for w in warnings))
        # Did not inject refocus as a send
        self.assertFalse(any("Still-unresolved checklist" in m for m in sent))

    def test_accept_refocus_rewrites_next_message(self):
        confirms, warnings, sent, store = self._run_drifting_loop(accept_refocus=True)
        self.assertTrue(confirms)
        self.assertTrue(
            any("Still-unresolved checklist" in m for m in sent),
            sent,
        )
        self.assertEqual(
            len([n for n in store.list() if n.signals.get("drift_observed")]),
            0,
        )

    def test_accept_stop_here_ends_loop_when_task_complete(self):
        """Post-fix creep: accept 'Stop here?' clears reflection and ends turn."""
        from aider.coders.base_coder import Coder
        from aider.z.uncertainty.engine import SessionContext, UncertaintyEngine
        from aider.z.uncertainty.store import UncertaintyStore

        root = Path(tempfile.mkdtemp(prefix="z_drift_done_"))
        store = UncertaintyStore(root=root, repo_key=str(root))
        eng = UncertaintyEngine(SessionContext(root=root, store=store))
        eng.ctx.checklist = TaskChecklist(
            task_id="lru",
            title="Fix LRU leak",
            items=[
                RequirementItem(
                    id="fix",
                    text="Fix memory leak in src/lru_cache.h",
                    status=STATUS_FULLY,
                    kind="product",
                ),
            ],
        )

        confirms = []
        outputs = []

        class FakeIO:
            yes = None

            def tool_output(self, *a, **k):
                outputs.append(a[0] if a else "")

            def tool_warning(self, *a, **k):
                pass

            def tool_error(self, *a, **k):
                pass

            def confirm_ask(self, question, default="y", explicit_yes_required=False, **k):
                confirms.append(question)
                return True  # accept stop-here

        coder = Coder.__new__(Coder)
        coder.io = FakeIO()
        coder.verbose = False
        coder.max_reflections = 3
        coder.num_reflections = 0
        coder.reflected_message = None
        coder.aider_edited_files = set()
        coder.last_aider_commit_hash = None
        coder.last_verification = None
        coder._z_gate_hold_dirty = False
        coder.root = root
        coder.uncertainty_engine = eng
        coder.uncertainty_store = store
        coder._drift_asked_this_task = False
        coder._drift_reflection_log = []

        send_n = {"n": 0}
        sent = []

        def fake_init():
            coder.num_reflections = 0
            coder.reflected_message = None
            coder._drift_reflection_log = []
            coder.aider_edited_files = set()

        def fake_send(message):
            sent.append(message)
            send_n["n"] += 1
            # Same file re-touched each reflection (the live LRU creep shape)
            edited = {str(root / "src/lru_cache.h")}
            coder._last_send_edited_files = set(edited)
            coder.aider_edited_files.update(edited)
            if send_n["n"] == 1:
                coder.reflected_message = "lint leftover"
            elif send_n["n"] <= 3:
                coder.reflected_message = "more lint"
            else:
                coder.reflected_message = None
            if False:
                yield None

        coder.init_before_message = fake_init
        coder.send_message = fake_send
        coder._maybe_pull_skills = lambda *a, **k: None
        coder._maybe_begin_uncertainty_task = lambda *a, **k: None
        coder._maybe_require_implementation_plan = lambda *a, **k: True
        coder._maybe_suggest_skill = lambda *a, **k: None
        coder.get_rel_fname = lambda p: os.path.relpath(str(p), str(root))

        with mock.patch(
            "aider.z.uncertainty.gate.report_auto_fix_exhaustion",
            return_value=None,
        ):
            coder.run_one("Fix the LRU cache memory leak", preproc=False)

        self.assertTrue(confirms)
        self.assertIn("Stop here?", confirms[0])
        self.assertTrue(any("stopping further" in o.lower() for o in outputs))
        # No refocus rewrite pushed into a later send
        self.assertFalse(any("Still-unresolved checklist" in m for m in sent))
        # Loop stopped — did not keep reflecting forever
        self.assertLessEqual(send_n["n"], 3)


if __name__ == "__main__":
    unittest.main()
