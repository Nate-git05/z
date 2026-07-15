"""Batch-2 eval hardening: process requirements, risk labels, quality_state, CLI smoke."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_eval2_")
os.environ["Z_HOME"] = _HOME

from aider.z.skills.router import collect_repo_signals, route_skill  # noqa: E402
from aider.z.skills.schema import Skill  # noqa: E402
from aider.z.uncertainty.checklist import (  # noqa: E402
    bind_evidence,
    classify_requirement_kind,
    decompose_request,
    rescore_checklist_with_evidence,
)
from aider.z.uncertainty.detectors import detect_requirement_gaps  # noqa: E402
from aider.z.uncertainty.gate import _effective_gate_tier, _format_node_lines  # noqa: E402
from aider.z.uncertainty.risk import collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeStatus,
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
    UncertaintyNode,
)
from aider.z.uncertainty.verify import (  # noqa: E402
    VerificationRecord,
    VerifyState,
    discover_cli_modules,
    run_smoke_cli,
)


class ProcessRequirementTest(unittest.TestCase):
    def test_classify_use_uncertainty_as_process(self):
        self.assertEqual(
            classify_requirement_kind("Use uncertainty before committing"),
            "process",
        )
        self.assertEqual(
            classify_requirement_kind("Add Stripe checkout endpoint"),
            "product",
        )

    def test_process_req_satisfied_from_execution_log_not_source(self):
        checklist = decompose_request(
            "Task",
            "- Build a Python CLI\n- Use uncertainty during the work\n",
        )
        process_items = [i for i in checklist.items if i.kind == "process"]
        self.assertTrue(process_items, msg=[(i.text, i.kind) for i in checklist.items])

        # No product files mention "uncertainty"
        evidence = bind_evidence(
            checklist,
            files_changed=["cli.py"],
            file_contents={"cli.py": "def main():\n    print('hi')\n"},
            symbols=["main"],
            test_files=[],
            execution_log="Uncertainty tree: 2 new node(s). Verification state=TESTS_PASSED",
        )
        rescore_checklist_with_evidence(checklist, evidence)
        for item in process_items:
            self.assertEqual(
                item.status,
                "Fully Addressed",
                msg=f"{item.text} should be satisfied from session log",
            )

    def test_process_gap_is_low_not_high_block(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="x",
            items=[
                RequirementItem(
                    text="Use uncertainty",
                    status="Not Addressed",
                    kind="process",
                )
            ],
        )
        sig = collect_base_signals(["cli.py"])
        nodes = detect_requirement_gaps(sig, checklist=checklist)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].risk_tier, Tier.LOW)
        self.assertEqual(_effective_gate_tier(nodes[0]), Tier.LOW)


class RiskLabelConsistencyTest(unittest.TestCase):
    def test_format_uses_effective_gate_tier(self):
        node = UncertaintyNode(
            title="Requirement gap: listing",
            type=NodeType.REQUIREMENT_GAP,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.MEDIUM,  # stored medium…
            summary="s",
            signals={"requirement_status": "Not Addressed", "requirement_kind": "product"},
        )
        # …but gate treats Not Addressed product as High
        self.assertEqual(_effective_gate_tier(node), Tier.HIGH)
        text = _format_node_lines([node])
        self.assertIn("[High]", text)
        self.assertNotIn("[Medium]", text)


class SkillQualityStateTest(unittest.TestCase):
    def test_draft_and_rejected_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app.py").write_text("x=1\n", encoding="utf-8")
            sig = collect_repo_signals(root)
            draft = Skill(
                title="Auth",
                description="auth",
                content="Use JWT",
                languages=["python"],
                quality_state="draft",
                needs_review=True,
            )
            rejected = Skill(
                title="Go scaffold",
                description="go",
                content="go mod",
                languages=["python"],
                quality_state="rejected",
            )
            self.assertFalse(route_skill(draft, "fix auth", sig, score=0.9).apply)
            self.assertFalse(route_skill(rejected, "fix auth", sig, score=0.9).apply)


class CliSmokeTest(unittest.TestCase):
    def test_discover_cli_modules(self):
        mods = discover_cli_modules(
            Path("."),
            ["pkg/__main__.py", "pkg/cli.py", "lib/util.py"],
        )
        self.assertIn("pkg", mods)
        self.assertTrue(any("cli" in m for m in mods))

    def test_run_smoke_cli_help_exit_zero(self):
        ok, detail = run_smoke_cli(Path("/workspace"), module="aider.z.cli", args="--help")
        self.assertTrue(ok, msg=detail)
        self.assertIn("exit=0", detail)


if __name__ == "__main__":
    unittest.main()
