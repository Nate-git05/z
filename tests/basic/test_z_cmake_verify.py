"""CMake verify: reconfigure on build-file edits; refuse stale suite-only green."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aider.z.uncertainty.cmake_verify import (
    apply_cmake_result_to_record,
    cmake_reconfigure_enabled,
    edited_build_system_files,
    extract_expected_tests_from_cmake_text,
    extract_expected_tests_from_edited,
    is_build_system_path,
    match_change_tests,
    parse_ctest_test_names,
    prepare_cmake_verify,
)
from aider.z.uncertainty.verify import (
    VerificationRecord,
    VerifyState,
    detect_test_command,
    verify_edits,
)


class BuildSystemPathTest(unittest.TestCase):
    def test_cmakelists_and_cmake(self):
        self.assertTrue(is_build_system_path("CMakeLists.txt"))
        self.assertTrue(is_build_system_path("cmake/Foo.cmake"))
        self.assertFalse(is_build_system_path("src/foo.c"))

    def test_edited_build_files(self):
        got = edited_build_system_files(
            ["src/a.c", "CMakeLists.txt", "cmake/Helpers.cmake"]
        )
        self.assertEqual(got, ["CMakeLists.txt", "cmake/Helpers.cmake"])


class ParseCtestNamesTest(unittest.TestCase):
    def test_ctest_n_output(self):
        out = """
Test project /tmp/proj/build
  Test #1: miniregex_tests
  Test #2: minilfu_tests

Total Tests: 2
"""
        self.assertEqual(
            parse_ctest_test_names(out), ["miniregex_tests", "minilfu_tests"]
        )

    def test_extract_add_test(self):
        text = """
add_executable(minilfu_tests tests/minilfu_tests.c)
add_test(NAME minilfu_tests COMMAND minilfu_tests)
"""
        names = extract_expected_tests_from_cmake_text(text)
        self.assertIn("minilfu_tests", names)


class MatchChangeTestsTest(unittest.TestCase):
    def test_match(self):
        self.assertEqual(
            match_change_tests(["minilfu_tests"], ["miniregex_tests", "minilfu_tests"]),
            ["minilfu_tests"],
        )
        self.assertEqual(match_change_tests(["minilfu_tests"], ["miniregex_tests"]), [])


class PrepareCmakeVerifyTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("Z_CMAKE_RECONFIGURE", None)
        os.environ.pop("Z_CMAKE_REQUIRE_MATCHED", None)
        self.root = Path(tempfile.mkdtemp(prefix="z_cmake_"))
        (self.root / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.10)\n"
            "project(demo C)\n"
            "add_executable(miniregex_tests t1.c)\n"
            "add_test(NAME miniregex_tests COMMAND miniregex_tests)\n",
            encoding="utf-8",
        )
        (self.root / "build").mkdir()
        # Stale cache marker
        (self.root / "build" / "CMakeCache.txt").write_text("# stale\n", encoding="utf-8")

    def tearDown(self):
        os.environ.pop("Z_CMAKE_RECONFIGURE", None)
        os.environ.pop("Z_CMAKE_REQUIRE_MATCHED", None)

    def test_reconfigure_invoked_before_ctest_n(self):
        # Edit CMakeLists to add minilfu_tests
        (self.root / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.10)\n"
            "project(demo C)\n"
            "add_executable(miniregex_tests t1.c)\n"
            "add_test(NAME miniregex_tests COMMAND miniregex_tests)\n"
            "add_executable(minilfu_tests t2.c)\n"
            "add_test(NAME minilfu_tests COMMAND minilfu_tests)\n",
            encoding="utf-8",
        )
        calls = []

        def fake_run(cmd, verbose=False, error_print=None, cwd=None):
            calls.append(cmd)
            if "cmake -S" in cmd:
                return 0, "Configuring done"
            if "ctest -N" in cmd:
                # After reconfigure, both tests visible
                return (
                    0,
                    "Test project\n  Test #1: miniregex_tests\n"
                    "  Test #2: minilfu_tests\nTotal Tests: 2\n",
                )
            return 0, ""

        result = prepare_cmake_verify(
            self.root,
            ["CMakeLists.txt"],
            test_cmd="ctest --test-dir build",
            non_interactive=True,
            run_cmd_fn=fake_run,
        )
        self.assertTrue(result.applies)
        self.assertTrue(result.build_dirty)
        self.assertTrue(result.reconfigured)
        self.assertTrue(any("cmake -S" in c for c in calls))
        self.assertTrue(any("ctest -N" in c for c in calls))
        # cmake -S should come before ctest -N
        cmake_i = next(i for i, c in enumerate(calls) if "cmake -S" in c)
        n_i = next(i for i, c in enumerate(calls) if "ctest -N" in c)
        self.assertLess(cmake_i, n_i)
        self.assertIn("minilfu_tests", result.discovered_tests)
        self.assertIn("minilfu_tests", result.matched_change_tests)
        self.assertFalse(result.stale_suite)

    def test_stale_suite_when_new_test_missing(self):
        (self.root / "CMakeLists.txt").write_text(
            "add_test(NAME minilfu_tests COMMAND minilfu_tests)\n",
            encoding="utf-8",
        )

        def fake_run(cmd, verbose=False, error_print=None, cwd=None):
            if "cmake -S" in cmd:
                return 0, "ok"
            if "ctest -N" in cmd:
                # Stale: only old suite
                return 0, "Test #1: miniregex_tests\nTotal Tests: 1\n"
            return 0, ""

        result = prepare_cmake_verify(
            self.root,
            ["CMakeLists.txt"],
            non_interactive=True,
            run_cmd_fn=fake_run,
        )
        self.assertTrue(result.stale_suite)
        self.assertIn("minilfu_tests", result.error)
        self.assertEqual(result.matched_change_tests, [])

    def test_reconfigure_failure_recorded(self):
        def fake_run(cmd, verbose=False, error_print=None, cwd=None):
            if "cmake -S" in cmd:
                return 1, "CMake Error: something"
            return 0, ""

        result = prepare_cmake_verify(
            self.root,
            ["CMakeLists.txt"],
            non_interactive=True,
            run_cmd_fn=fake_run,
        )
        self.assertTrue(result.reconfigure_failed)
        self.assertIn("reconfigure failed", result.error.lower())

    def test_escape_skips_reconfigure(self):
        os.environ["Z_CMAKE_RECONFIGURE"] = "0"
        self.assertFalse(cmake_reconfigure_enabled())

        def fake_run(cmd, verbose=False, error_print=None, cwd=None):
            if "ctest -N" in cmd:
                return 0, "Test #1: miniregex_tests\n"
            raise AssertionError(f"unexpected cmd: {cmd}")

        result = prepare_cmake_verify(
            self.root,
            ["CMakeLists.txt"],
            non_interactive=True,
            run_cmd_fn=fake_run,
        )
        self.assertFalse(result.reconfigured)
        self.assertFalse(result.reconfigure_attempted)


class VerifyEditsCmakeIntegrationTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("Z_CMAKE_RECONFIGURE", None)
        os.environ.pop("Z_CMAKE_REQUIRE_MATCHED", None)
        self.root = Path(tempfile.mkdtemp(prefix="z_cmake_int_"))
        (self.root / "CMakeLists.txt").write_text(
            "add_test(NAME minilfu_tests COMMAND minilfu_tests)\n",
            encoding="utf-8",
        )
        (self.root / "build").mkdir()

    def tearDown(self):
        os.environ.pop("Z_CMAKE_RECONFIGURE", None)
        os.environ.pop("Z_CMAKE_REQUIRE_MATCHED", None)

    def test_verify_edits_fails_closed_on_stale_suite(self):
        def fake_run(cmd, verbose=False, error_print=None, cwd=None):
            if "cmake -S" in cmd:
                return 0, "ok"
            if "ctest -N" in cmd:
                return 0, "Test #1: miniregex_tests\nTotal Tests: 1\n"
            return 0, "100% tests passed, 0 tests failed out of 1\n"

        with patch("aider.run_cmd.run_cmd", side_effect=fake_run):
            with patch(
                "aider.z.uncertainty.cmake_verify._default_run_cmd",
                return_value=fake_run,
            ):
                record, _ = verify_edits(
                    self.root,
                    ["CMakeLists.txt"],
                    skip_smoke=True,
                    skip_package_prechecks=True,
                    skip_type_members=True,
                    skip_relevant_execution=True,
                    non_interactive=True,
                )
        self.assertEqual(record.failure_kind, "cmake_stale_suite")
        self.assertFalse(record.meaningful_pass)
        self.assertTrue(record.reconfigured)
        self.assertIn("minilfu_tests", record.cmake_expected_tests)

    def test_detect_test_command_cmake_without_build(self):
        root = Path(tempfile.mkdtemp(prefix="z_cmake_nobuild_"))
        (root / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
        self.assertEqual(detect_test_command(root), "ctest --test-dir build")

    def test_apply_cmake_result_to_record(self):
        from aider.z.uncertainty.cmake_verify import CMakeVerifyResult

        rec = VerificationRecord(ran=True, passed=True, state=VerifyState.TESTS_PASSED)
        cv = CMakeVerifyResult(
            applies=True,
            reconfigured=True,
            discovered_tests=["a", "b"],
            matched_change_tests=["b"],
            reconfigure_command='cmake -S "." -B "build"',
            expected_tests=["b"],
        )
        # expected_tests field on result is expected_tests; apply uses cmake_expected
        cv.expected_tests = ["b"]
        apply_cmake_result_to_record(rec, cv)
        self.assertTrue(rec.reconfigured)
        self.assertEqual(rec.discovered_tests, ["a", "b"])
        self.assertEqual(rec.tests_discovered, 2)


class ExtractExpectedFromEditedTest(unittest.TestCase):
    def test_from_file_and_stem(self):
        root = Path(tempfile.mkdtemp(prefix="z_cmake_exp_"))
        (root / "CMakeLists.txt").write_text(
            "add_test(NAME foo_tests COMMAND foo_tests)\n", encoding="utf-8"
        )
        names = extract_expected_tests_from_edited(
            root, ["CMakeLists.txt", "tests/bar_tests.c"]
        )
        self.assertIn("foo_tests", names)
        self.assertIn("bar_tests", names)


if __name__ == "__main__":
    unittest.main()
