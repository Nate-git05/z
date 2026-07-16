"""Dependency fabrication defenses — freezegun-style shadow packages."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_dep_fab_")
os.environ["Z_HOME"] = _HOME

from aider.z.deps import (  # noqa: E402
    collect_declared_dependencies,
    extract_missing_modules,
    is_dependency_fabrication,
    scan_paths_for_fabrication,
    top_level_module_name,
)
from aider.z.uncertainty.detectors import detect_dependency_fabrication  # noqa: E402
from aider.z.uncertainty.gate import _effective_gate_tier  # noqa: E402
from aider.z.uncertainty.risk import DetectionSignals, collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import NodeType, Tier, UncertaintyNode  # noqa: E402


class DepParseTest(unittest.TestCase):
    def test_extract_module_not_found(self):
        text = "E   ModuleNotFoundError: No module named 'freezegun'\n"
        self.assertIn("freezegun", extract_missing_modules(text))

    def test_collect_from_requirements(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requirements-test.txt").write_text(
                "pytest>=7\nfreezegun>=1.2\ncolorama\n", encoding="utf-8"
            )
            names = collect_declared_dependencies(root)
            self.assertIn("freezegun", names)
            self.assertIn("colorama", names)

    def test_top_level_module_name(self):
        self.assertEqual(top_level_module_name("freezegun/__init__.py"), "freezegun")
        self.assertEqual(top_level_module_name("freezegun.py"), "freezegun")
        self.assertIsNone(top_level_module_name("tests/test_x.py"))
        self.assertIsNone(top_level_module_name("src/freezegun/__init__.py"))


class FabricationSignalTest(unittest.TestCase):
    def test_flags_new_freezegun_after_missing_import(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requirements-test.txt").write_text("freezegun\n", encoding="utf-8")
            (root / "app.py").write_text("print(1)\n", encoding="utf-8")
            reason = is_dependency_fabrication(
                "freezegun/__init__.py",
                root=root,
                missing_modules={"freezegun"},
            )
            self.assertIsNotNone(reason)
            self.assertIn("freezegun", reason.lower())

    def test_allows_normal_new_module(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
            reason = is_dependency_fabrication(
                "myapp/helpers.py",
                root=root,
                missing_modules={"freezegun"},
            )
            self.assertIsNone(reason)

    def test_scan_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "dev").mkdir()
            (root / "dev" / "requirements-test.txt").write_text(
                "freezegun==1.5\n", encoding="utf-8"
            )
            hits = scan_paths_for_fabrication(
                ["freezegun/__init__.py", "tests/test_feature.py"],
                root=root,
                missing_modules=["freezegun"],
            )
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["package"], "freezegun")


class DetectorAndGateTest(unittest.TestCase):
    def test_detector_emits_high_node(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requirements.txt").write_text("freezegun\n", encoding="utf-8")
            sig = collect_base_signals(["freezegun/__init__.py"])
            nodes = detect_dependency_fabrication(
                sig,
                root=root,
                files_changed=["freezegun/__init__.py"],
                execution_log="ModuleNotFoundError: No module named 'freezegun'",
            )
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].type, NodeType.DEPENDENCY_FABRICATION)
            self.assertEqual(nodes[0].risk_tier, Tier.HIGH)
            self.assertEqual(_effective_gate_tier(nodes[0]), Tier.HIGH)

    def test_force_commit_env_refused_for_fabrication(self):
        """--force-commit alone must not clear dependency fabrication."""
        from aider.z.uncertainty.gate import prepare_commit
        from aider.z.uncertainty.verify import VerificationRecord, VerifyState

        node = UncertaintyNode(
            title="Dependency fabrication — local 'freezegun'",
            type=NodeType.DEPENDENCY_FABRICATION,
            confidence_tier=Tier.LOW,
            risk_tier=Tier.HIGH,
            summary="shadow",
            signals={
                "dependency_fabrication": True,
                "fabricated_package": "freezegun",
            },
        )

        class _Store:
            def list(self, include_resolved=False):
                return [node]

            def get(self, _id):
                return node

            def add(self, n, sync=False):
                return n

            def update_status(self, *_a, **_k):
                return node

        class _IO:
            def tool_error(self, *a, **k):
                pass

            def tool_warning(self, *a, **k):
                pass

            def tool_output(self, *a, **k):
                pass

            def confirm_ask(self, *a, **k):
                return False  # user refuses typed ack

        class _Eng:
            class ctx:
                current_task_id = None
                current_task_title = None
                last_verification = None
                execution_log = ""
                checklist = None
                assumed_apis = set()
                live_verified_apis = set()
                mcp_unverifiable = set()
                edge_cases_from_model = []
                discussed_text = ""
                last_diff = ""
                user_decisions = []
                migration_data_impact = None
                pattern_results = {}
                session_id = "s"
                user_label = None
                root = Path(".")

            def record_execution(self, *_a, **_k):
                pass

            def analyze_edits(self, *_a, **_k):
                return [node]

        class _Coder:
            verify_commit_gate = True
            test_cmd = None
            verbose = False
            io = _IO()
            uncertainty_engine = _Eng()
            uncertainty_store = _Store()
            last_verification = None
            test_outcome = None
            force_commit = True  # --force-commit
            root = "."
            partial_response_content = ""

            def get_rel_fname(self, p):
                return str(p)

        with mock.patch(
            "aider.z.uncertainty.gate.verify_edits",
            return_value=(
                VerificationRecord(
                    ran=True,
                    exit_code=0,
                    tests_discovered=7,
                    tests_passed=7,
                    passed=True,
                    state=VerifyState.TESTS_PASSED,
                ),
                ["tests/test_x.py"],
            ),
        ), mock.patch(
            "aider.z.uncertainty.gate.gate_enabled", return_value=True
        ), mock.patch.dict(os.environ, {"Z_FORCE_COMMIT": "1"}, clear=False):
            # analyze returns fab node; force env set but confirm refused
            result = prepare_commit(_Coder(), ["freezegun/__init__.py"])
        self.assertFalse(result.allow_commit)
        self.assertTrue(
            any(
                n.type == NodeType.DEPENDENCY_FABRICATION for n in (result.blocked_high or [])
            )
            or "fabrication" in (result.reason or "").lower()
            or result.reason == "high-risk blockers"
            or result.reason == "dependency fabrication blockers"
        )


class PromptRuleTest(unittest.TestCase):
    def test_dependency_rule_on_prompts(self):
        from aider.coders.base_prompts import CoderPrompts

        self.assertIn("freezegun", CoderPrompts.dependency_fabrication_prompt)
        self.assertIn("do NOT create", CoderPrompts.dependency_fabrication_prompt)


class PreWriteBlockTest(unittest.TestCase):
    def test_blocks_create_shadow_package(self):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "requirements-test.txt").write_text("freezegun\n", encoding="utf-8")

            class _C:
                abs_root_path_cache = {}
                abs_fnames = set()
                repo = None
                dry_run = True
                auto_commits = False
                warning_given = False
                uncertainty_engine = None
                done_messages = [
                    {
                        "role": "user",
                        "content": "ModuleNotFoundError: No module named 'freezegun'",
                    }
                ]

                path_under_root = Coder.path_under_root
                abs_root_path = Coder.abs_root_path
                allowed_to_edit = Coder.allowed_to_edit
                _blocks_dependency_fabrication = Coder._blocks_dependency_fabrication
                check_for_dirty_commit = lambda self, p: None
                check_added_files = lambda self: None

                def get_rel_fname(self, p):
                    return str(Path(p).relative_to(self.root))

            c = _C()
            c.root = str(root)
            c.io = InputOutput(pretty=False, yes=True)
            target = root / "freezegun" / "__init__.py"
            # path doesn't exist yet
            self.assertFalse(target.exists())
            with mock.patch.object(c.io, "tool_error") as err:
                allowed = c.allowed_to_edit(str(target))
            self.assertFalse(allowed)
            self.assertTrue(err.called)


if __name__ == "__main__":
    unittest.main()
