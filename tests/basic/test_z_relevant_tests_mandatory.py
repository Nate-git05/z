"""Mandatory pre-existing relevant-test execution (langchain-class miss)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_relevant_mandatory_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.detectors import (  # noqa: E402
    classify_relevant_tests,
    find_relevant_tests,
)
from aider.z.uncertainty.gate import _reflect_fix_tests  # noqa: E402
from aider.z.uncertainty.verify import (  # noqa: E402
    VerificationRecord,
    VerifyState,
    build_relevant_test_command,
    verify_edits,
)


class FindRelevantNestedTest(unittest.TestCase):
    def test_finds_implementations_nested_test_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "pkg" / "agents" / "middleware"
            src.mkdir(parents=True)
            (src / "model_retry.py").write_text(
                "class ModelRetryMiddleware:\n    pass\n", encoding="utf-8"
            )
            nested = (
                root
                / "tests"
                / "unit_tests"
                / "agents"
                / "middleware"
                / "implementations"
            )
            nested.mkdir(parents=True)
            (nested / "test_model_retry.py").write_text(
                "from pkg.agents.middleware.model_retry import ModelRetryMiddleware\n"
                "def test_model_retry_specific_exceptions():\n    assert True\n",
                encoding="utf-8",
            )
            # Wrong-location new test (should also be findable, but nested is key)
            wrong = root / "tests" / "unit_tests" / "agents" / "middleware"
            wrong.mkdir(parents=True, exist_ok=True)
            (wrong / "test_model_retry_new.py").write_text(
                "def test_new():\n    assert True\n", encoding="utf-8"
            )

            found = find_relevant_tests(
                root,
                ["pkg/agents/middleware/model_retry.py"],
                symbols=["ModelRetryMiddleware"],
            )
            self.assertTrue(
                any("implementations/test_model_retry.py" in f for f in found),
                found,
            )


class ClassifyRelevantTest(unittest.TestCase):
    def test_new_files_split(self):
        preexisting, newly = classify_relevant_tests(
            [
                "tests/implementations/test_model_retry.py",
                "tests/middleware/test_model_retry_new.py",
            ],
            edited=[
                "pkg/model_retry.py",
                "tests/middleware/test_model_retry_new.py",
            ],
            new_files=["tests/middleware/test_model_retry_new.py"],
        )
        self.assertEqual(
            preexisting, ["tests/implementations/test_model_retry.py"]
        )
        self.assertEqual(newly, ["tests/middleware/test_model_retry_new.py"])

    def test_without_new_files_all_preexisting(self):
        preexisting, newly = classify_relevant_tests(
            ["tests/a/test_x.py", "tests/b/test_x_new.py"],
            edited=["src/x.py", "tests/b/test_x_new.py"],
            new_files=[],
        )
        self.assertEqual(len(preexisting), 2)
        self.assertEqual(newly, [])


class BuildCommandTest(unittest.TestCase):
    def test_pytest_appends_files(self):
        cmd = build_relevant_test_command(
            "python -m pytest -q",
            ["tests/implementations/test_model_retry.py"],
        )
        self.assertIn("pytest", cmd)
        self.assertIn("tests/implementations/test_model_retry.py", cmd)


class MeaningfulPassRequiresPreexistingTest(unittest.TestCase):
    def test_new_tests_alone_do_not_meaningful_pass(self):
        rec = VerificationRecord(
            ran=True,
            command="python -m pytest -q tests/new/test_only.py",
            exit_code=0,
            tests_discovered=2,
            passed=True,
            state=VerifyState.TESTS_PASSED,
            relevant_preexisting=[
                "tests/implementations/test_model_retry.py",
            ],
            relevant_newly_written=["tests/new/test_only.py"],
            relevant_ran=False,
            relevant_passed=None,
        )
        self.assertFalse(rec.meaningful_pass)

    def test_preexisting_green_allows_pass(self):
        rec = VerificationRecord(
            ran=True,
            command="python -m pytest -q",
            exit_code=0,
            tests_discovered=3,
            passed=True,
            state=VerifyState.TESTS_PASSED,
            relevant_preexisting=[
                "tests/implementations/test_model_retry.py",
            ],
            relevant_ran=True,
            relevant_passed=True,
        )
        self.assertTrue(rec.meaningful_pass)


class VerifyRunsPreexistingTest(unittest.TestCase):
    def test_targeted_preexisting_failure_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "pkg"
            src.mkdir()
            (src / "__init__.py").write_text("", encoding="utf-8")
            (src / "model_retry.py").write_text(
                "class ModelRetryMiddleware:\n    x = 1\n", encoding="utf-8"
            )
            nested = root / "tests" / "implementations"
            nested.mkdir(parents=True)
            (nested / "test_model_retry.py").write_text(
                "from pkg.model_retry import ModelRetryMiddleware\n"
                "def test_model_retry_specific_exceptions():\n"
                "    assert ModelRetryMiddleware.x == 0  # old contract\n",
                encoding="utf-8",
            )
            (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            new_t = root / "tests" / "middleware"
            new_t.mkdir(parents=True)
            (new_t / "test_model_retry_new.py").write_text(
                "def test_new_green():\n    assert True\n", encoding="utf-8"
            )

            record, relevant = verify_edits(
                root,
                [
                    "pkg/model_retry.py",
                    "tests/middleware/test_model_retry_new.py",
                ],
                symbols=["ModelRetryMiddleware"],
                new_files=["tests/middleware/test_model_retry_new.py"],
                skip_smoke=True,
                skip_package_prechecks=True,
                skip_type_members=True,
            )
            self.assertTrue(
                any("implementations/test_model_retry.py" in r for r in relevant),
                relevant,
            )
            self.assertTrue(record.relevant_preexisting)
            self.assertTrue(record.relevant_ran)
            self.assertFalse(record.relevant_passed)
            self.assertFalse(record.meaningful_pass)
            self.assertEqual(record.failure_kind, "relevant_tests")


class ReflectMentionsPreexistingTest(unittest.TestCase):
    def test_reflect_lists_preexisting(self):
        rec = VerificationRecord(
            ran=True,
            command="pytest tests/implementations/test_model_retry.py",
            exit_code=1,
            state=VerifyState.TESTS_FAILED,
            failure_kind="relevant_tests",
            relevant_preexisting=[
                "tests/implementations/test_model_retry.py",
            ],
            relevant_passed=False,
            output_excerpt="AssertionError",
        )
        msg = _reflect_fix_tests(rec, ["pkg/model_retry.py"])
        self.assertIn("MANDATORY pre-existing", msg)
        self.assertIn("implementations/test_model_retry.py", msg)


if __name__ == "__main__":
    unittest.main()
