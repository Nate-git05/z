"""Reflection-safe cheap detectors — absorption/siblings without clean exit."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_cheap_det_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.engine import SessionContext, UncertaintyEngine  # noqa: E402
from aider.z.uncertainty.schema import NodeType  # noqa: E402
from aider.z.uncertainty.store import UncertaintyStore  # noqa: E402


_PRIORITY_DIFF = (
    "diff --git a/event_queue.py b/event_queue.py\n"
    "--- a/event_queue.py\n"
    "+++ b/event_queue.py\n"
    "@@ -1,8 +1,12 @@\n"
    " class Event:\n"
    "-    def __init__(self, name):\n"
    "+    def __init__(self, name, priority=\"normal\"):\n"
    "         self.name = name\n"
    "+        self.priority = priority\n"
    "\n"
    " def process(event):\n"
    "-    return event.name\n"
    "+    # Absorbs AttributeError for callers that omit the new param\n"
    "+    prio = getattr(event, \"priority\", \"normal\")\n"
    "+    return event.name, prio\n"
)


class CheapOnlyAnalyzeTest(unittest.TestCase):
    def _engine(self, root: Path) -> UncertaintyEngine:
        store = UncertaintyStore(root=root, repo_key=str(root))
        ctx = SessionContext(root=root, store=store, session_id="cheap-1")
        eng = UncertaintyEngine(ctx)
        eng.begin_task("Add event priority")
        return eng

    def test_cheap_only_flags_getattr_new_param(self):
        """event_queue live miss shape — must fire without a clean-exit turn."""
        root = Path(tempfile.mkdtemp(prefix="z_cheap_abs_"))
        eq = root / "event_queue.py"
        eq.write_text(
            "class Event:\n"
            '    def __init__(self, name, priority="normal"):\n'
            "        self.name = name\n"
            "        self.priority = priority\n"
            "\n"
            "def process(event):\n"
            '    prio = getattr(event, "priority", "normal")\n'
            "    return event.name, prio\n",
            encoding="utf-8",
        )
        eng = self._engine(root)
        eng.record_diff(_PRIORITY_DIFF)
        with mock.patch(
            "aider.z.uncertainty.engine.detect_edge_cases",
            side_effect=AssertionError("edge_cases must not run under cheap_only"),
        ) as edge_mock:
            nodes = eng.analyze_edits(
                ["event_queue.py"],
                cheap_only=True,
                run_gap_analysis=False,
                diff=_PRIORITY_DIFF,
            )
        edge_mock.assert_not_called()
        abs_nodes = [
            n
            for n in nodes
            if n.type in (NodeType.GETATTR_SHORTCUT, NodeType.FAILURE_ABSORPTION)
            or n.signals.get("absorption_pattern_id") == "getattr_new_param_default"
        ]
        self.assertTrue(
            abs_nodes,
            f"expected getattr absorption node, got {[n.type for n in nodes]}",
        )

    def test_full_pipeline_still_invokes_edge_cases(self):
        root = Path(tempfile.mkdtemp(prefix="z_cheap_full_"))
        (root / "mod.py").write_text("x = 1\n", encoding="utf-8")
        eng = self._engine(root)
        with mock.patch(
            "aider.z.uncertainty.engine.detect_edge_cases",
            return_value=[],
        ) as edge_mock:
            eng.analyze_edits(["mod.py"], cheap_only=False, diff="")
        edge_mock.assert_called_once()

    def test_cheap_only_skips_gap_analysis_model_path(self):
        root = Path(tempfile.mkdtemp(prefix="z_cheap_gap_"))
        (root / "mod.py").write_text("x = 1\n", encoding="utf-8")
        eng = self._engine(root)
        from aider.z.uncertainty.schema import RequirementItem, TaskChecklist
        from aider.z.uncertainty.evidence_strategy import STATUS_NOT

        eng.ctx.checklist = TaskChecklist(
            task_id="t",
            title="t",
            items=[
                RequirementItem(
                    id="i1",
                    text="Do the thing in mod.py",
                    status=STATUS_NOT,
                )
            ],
        )
        called = {"bind": False}

        def boom(*a, **k):
            called["bind"] = True
            raise AssertionError("gap analysis must not run under cheap_only")

        with mock.patch(
            "aider.z.uncertainty.engine.bind_evidence", side_effect=boom
        ):
            eng.analyze_edits(
                ["mod.py"],
                cheap_only=True,
                run_gap_analysis=True,  # even if requested, cheap_only wins
            )
        self.assertFalse(called["bind"])


class ReflectionHookTest(unittest.TestCase):
    def test_run_one_calls_cheap_detectors_alongside_rescore(self):
        from aider.coders.base_coder import Coder

        coder = Coder.__new__(Coder)
        coder.num_reflections = 0
        coder.max_reflections = 3
        coder.reflected_message = None
        coder.aider_edited_files = set()
        coder._drift_asked_this_task = False
        coder._drift_reflection_log = []
        coder.uncertainty_engine = mock.MagicMock()

        calls = []

        def fake_init():
            coder.num_reflections = 0
            coder.reflected_message = None

        def fake_send(message):
            coder._last_send_edited_files = {"/tmp/event_queue.py"}
            coder.aider_edited_files.add("/tmp/event_queue.py")
            # One reflection then stop — never clean-exit analyze
            if coder.num_reflections == 0:
                coder.reflected_message = "keep going"
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
        coder._rescore_checklist_for_drift = lambda: calls.append("rescore")
        coder._run_cheap_detectors_for_reflection = lambda: calls.append("cheap")
        coder._record_drift_reflection_turn = lambda **k: None
        coder._maybe_detect_drift = lambda: None
        coder.get_rel_fname = lambda p: "event_queue.py"

        with mock.patch(
            "aider.z.uncertainty.gate.report_auto_fix_exhaustion"
        ):
            coder.run_one("add priority", preproc=False)

        self.assertIn("rescore", calls)
        self.assertIn("cheap", calls)
        self.assertGreaterEqual(calls.count("cheap"), 1)

    def test_run_cheap_detectors_passes_cheap_only(self):
        from aider.coders.base_coder import Coder

        coder = Coder.__new__(Coder)
        eng = mock.MagicMock()
        coder.uncertainty_engine = eng
        coder._last_send_edited_files = {"/repo/event_queue.py"}
        coder.aider_edited_files = set(coder._last_send_edited_files)
        coder.repo = None
        coder.test_outcome = None
        coder.get_rel_fname = lambda p: "event_queue.py"

        coder._run_cheap_detectors_for_reflection()
        eng.analyze_edits.assert_called_once()
        kwargs = eng.analyze_edits.call_args.kwargs
        self.assertTrue(kwargs.get("cheap_only"))
        self.assertFalse(kwargs.get("run_gap_analysis"))


if __name__ == "__main__":
    unittest.main()
