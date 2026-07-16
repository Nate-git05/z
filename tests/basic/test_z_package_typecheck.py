"""Package-scoped typecheck-first verification + local type-member ground truth."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HOME = tempfile.mkdtemp(prefix="z_pkg_typecheck_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.gate import (  # noqa: E402
    _reflect_fix_compiler,
    prepare_commit,
)
from aider.z.uncertainty.package_checks import (  # noqa: E402
    discover_package_checks,
    find_nearest_package_json,
    looks_like_compiler_output,
    looks_like_root_test_guard,
)
from aider.z.uncertainty.type_members import (  # noqa: E402
    check_local_type_members,
    parse_type_declarations,
)
from aider.z.uncertainty.verify import (  # noqa: E402
    VerifyState,
    verify_edits,
)


class PackageDiscoveryTest(unittest.TestCase):
    def test_nearest_package_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "packages" / "opencode"
            pkg.mkdir(parents=True)
            (pkg / "package.json").write_text(
                json.dumps({"scripts": {"typecheck": "tsc --noEmit", "test": "bun test"}}),
                encoding="utf-8",
            )
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "echo do not run from root && exit 1"}}),
                encoding="utf-8",
            )
            found = find_nearest_package_json(
                root, "packages/opencode/src/tool/foo.ts"
            )
            self.assertEqual(found, pkg / "package.json")

    def test_discovers_typecheck_before_test(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "packages" / "opencode"
            pkg.mkdir(parents=True)
            (pkg / "src").mkdir()
            (pkg / "package.json").write_text(
                json.dumps(
                    {
                        "scripts": {
                            "typecheck": "tsc --noEmit",
                            "test": "bun test",
                            "lint": "eslint .",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (pkg / "bun.lock").write_text("", encoding="utf-8")
            plan = discover_package_checks(
                root, ["packages/opencode/src/tool/foo.ts"]
            )
            self.assertEqual(len(plan.checks), 1)
            self.assertEqual(plan.checks[0].kind, "typecheck")
            self.assertIn("bun run typecheck", plan.checks[0].command)
            self.assertIsNotNone(plan.package_test)
            self.assertIn("bun run test", plan.package_test[1])

    def test_compiler_and_guard_heuristics(self):
        self.assertTrue(
            looks_like_compiler_output(
                "error TS2339: Property 'worktree' does not exist on type 'Context'"
            )
        )
        self.assertTrue(
            looks_like_root_test_guard(
                "Do not run npm test from the root — this is a monorepo"
            )
        )


class TypeMemberTest(unittest.TestCase):
    def test_parses_interface_members(self):
        text = (
            "export interface Context<M = Metadata> {\n"
            "  sessionID: string\n"
            "  directory: string\n"
            "  metadata: M\n"
            "}\n"
        )
        decls = parse_type_declarations("ctx.ts", text)
        self.assertIn("Context", decls)
        self.assertIn("sessionID", decls["Context"].members)
        self.assertIn("directory", decls["Context"].members)
        self.assertNotIn("worktree", decls["Context"].members)

    def test_flags_missing_local_member(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "packages" / "opencode" / "src"
            pkg.mkdir(parents=True)
            (pkg / "context.ts").write_text(
                "export interface Context {\n"
                "  sessionID: string\n"
                "  directory: string\n"
                "}\n",
                encoding="utf-8",
            )
            tool = pkg / "tool.ts"
            tool.write_text(
                "import type { Context } from './context'\n"
                "export async function run(ctx: Context) {\n"
                "  return ctx.worktree\n"
                "}\n",
                encoding="utf-8",
            )
            result = check_local_type_members(
                root, ["packages/opencode/src/tool.ts"]
            )
            self.assertFalse(result.passed)
            self.assertTrue(
                any(i.member == "worktree" and i.receiver_type == "Context" for i in result.issues),
                result.issues,
            )

    def test_allows_declared_member(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "src"
            pkg.mkdir()
            (pkg / "context.ts").write_text(
                "export interface Context {\n  directory: string\n}\n",
                encoding="utf-8",
            )
            (pkg / "tool.ts").write_text(
                "export function run(ctx: Context) {\n  return ctx.directory\n}\n",
                encoding="utf-8",
            )
            result = check_local_type_members(root, ["src/tool.ts"])
            self.assertTrue(result.passed)


class VerifyOrderTest(unittest.TestCase):
    def test_type_member_fails_before_tests(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "packages" / "app" / "src"
            pkg.mkdir(parents=True)
            (pkg.parent / "package.json").write_text(
                json.dumps({"scripts": {"typecheck": "tsc --noEmit", "test": "echo ok"}}),
                encoding="utf-8",
            )
            (pkg / "types.ts").write_text(
                "export interface Ctx { a: string }\n", encoding="utf-8"
            )
            (pkg / "use.ts").write_text(
                "export function f(ctx: Ctx) { return ctx.missing }\n",
                encoding="utf-8",
            )
            # Even if typecheck would run, member check should short-circuit first
            record, _ = verify_edits(
                root,
                ["packages/app/src/use.ts"],
                skip_smoke=True,
                skip_package_prechecks=True,
            )
            self.assertEqual(record.state, VerifyState.TYPE_MEMBER_FAILED)
            self.assertEqual(record.failure_kind, "type_member")

    def test_package_typecheck_runs_before_root_test(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "packages" / "app"
            pkg.mkdir(parents=True)
            (pkg / "src").mkdir()
            (pkg / "package.json").write_text(
                json.dumps({"scripts": {"typecheck": "node -e \"process.exit(1)\""}}),
                encoding="utf-8",
            )
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "scripts": {
                            "test": "node -e \"console.log('do not run from root'); process.exit(1)\""
                        }
                    }
                ),
                encoding="utf-8",
            )
            (pkg / "src" / "a.ts").write_text("export const x = 1\n", encoding="utf-8")

            # Skip type-member (no annotated accesses); force prechecks
            record, _ = verify_edits(
                root,
                ["packages/app/src/a.ts"],
                skip_smoke=True,
                skip_type_members=True,
            )
            self.assertEqual(record.state, VerifyState.TYPECHECK_FAILED)
            self.assertEqual(record.failure_kind, "typecheck")
            self.assertTrue(record.prechecks)


class CompilerReflectTest(unittest.TestCase):
    def test_reflect_mentions_re_read_types(self):
        from aider.z.uncertainty.verify import VerificationRecord

        rec = VerificationRecord(
            ran=True,
            command="bun run typecheck",
            exit_code=1,
            state=VerifyState.TYPECHECK_FAILED,
            failure_kind="typecheck",
            output_excerpt=(
                "error TS2339: Property 'worktree' does not exist on type 'Context'"
            ),
        )
        msg = _reflect_fix_compiler(rec, ["packages/opencode/src/tool.ts"])
        self.assertIn("COMPILER", msg)
        self.assertIn("re-read", msg.lower())
        self.assertIn("worktree", msg)
        self.assertNotIn("getattr", msg)


if __name__ == "__main__":
    unittest.main()
