"""docs_touched signal + permissive getattr shortcut detector (logveil lessons)."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_docs_getattr_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.checklist import (  # noqa: E402
    bind_evidence,
    files_touch_docs,
    path_looks_docs_artifact,
    rescore_checklist_with_evidence,
)
from aider.z.uncertainty.detectors import (  # noqa: E402
    detect_getattr_shortcuts,
    detect_requirement_gaps,
    reconcile_requirement_with_signals,
)
from aider.z.uncertainty.gate import _effective_gate_tier, _reflect_fix_tests  # noqa: E402
from aider.z.uncertainty.risk import DetectionSignals, collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
)
from aider.z.uncertainty.verify import VerificationRecord  # noqa: E402


class DocsTouchedSignalTest(unittest.TestCase):
    def test_path_helpers(self):
        self.assertTrue(path_looks_docs_artifact("README.md"))
        self.assertTrue(path_looks_docs_artifact("docs/usage.md"))
        self.assertTrue(path_looks_docs_artifact("CHANGELOG.md"))
        self.assertFalse(path_looks_docs_artifact("src/notes.md"))
        self.assertTrue(files_touch_docs(["cli.py", "README.md"]))
        self.assertFalse(files_touch_docs(["cli.py", "tests/test_cli.py"]))

    def test_collect_base_signals_sets_docs_touched(self):
        sig = collect_base_signals(["logveil/cli.py", "README.md"])
        self.assertTrue(sig.docs_touched)
        sig2 = collect_base_signals(["logveil/cli.py"])
        self.assertFalse(sig2.docs_touched)

    def test_untouched_readme_does_not_satisfy_docs_requirement(self):
        """logveil #1: docs requirement must not pass via pre-existing README."""
        checklist = TaskChecklist(
            task_id="t1",
            title="IPv4 redaction",
            items=[
                RequirementItem(
                    text="Update the README and changelog",
                    status="Not Addressed",
                    kind="documentation",
                )
            ],
        )
        evidence = bind_evidence(
            checklist,
            files_changed=["logveil/redact.py", "tests/test_redact.py"],
            file_contents={
                "logveil/redact.py": "def redact_ipv4(text): return text\n",
                "README.md": "# Logveil\n\nDocument redact usage here.\n",
                "CHANGELOG.md": "## Unreleased\n",
            },
        )
        rescore_checklist_with_evidence(checklist, evidence)
        self.assertEqual(checklist.items[0].status, "Not Addressed")

        sig = collect_base_signals(["logveil/redact.py", "tests/test_redact.py"])
        self.assertFalse(sig.docs_touched)
        nodes = detect_requirement_gaps(sig, checklist=checklist)
        self.assertTrue(nodes)
        self.assertEqual(nodes[0].type, NodeType.REQUIREMENT_GAP)

    def test_edited_readme_satisfies_docs_requirement(self):
        checklist = TaskChecklist(
            task_id="t1",
            title="IPv4 redaction",
            items=[
                RequirementItem(
                    text="Update the README and changelog",
                    status="Not Addressed",
                    kind="documentation",
                )
            ],
        )
        evidence = bind_evidence(
            checklist,
            files_changed=["logveil/redact.py", "README.md", "CHANGELOG.md"],
            file_contents={
                "logveil/redact.py": "def redact_ipv4(text): return text\n",
                "README.md": "# Logveil\n\nOpt-in IPv4 redaction via --redact-ipv4.\n",
                "CHANGELOG.md": "## Unreleased\n- add opt-in IPv4 redaction\n",
            },
        )
        rescore_checklist_with_evidence(checklist, evidence)
        self.assertEqual(checklist.items[0].status, "Fully Addressed")

        sig = collect_base_signals(["logveil/redact.py", "README.md", "CHANGELOG.md"])
        self.assertTrue(sig.docs_touched)
        self.assertEqual(
            reconcile_requirement_with_signals(checklist.items[0], sig),
            "Fully Addressed",
        )


class GetattrShortcutTest(unittest.TestCase):
    def test_flags_getattr_for_new_param(self):
        diff = (
            "diff --git a/logveil/cli.py b/logveil/cli.py\n"
            "--- a/logveil/cli.py\n"
            "+++ b/logveil/cli.py\n"
            "@@ -1,5 +1,8 @@\n"
            " def build_parser():\n"
            "+    parser.add_argument('--redact-ipv4', action='store_true')\n"
            "     return parser\n"
            "+\n"
            "+def run(args):\n"
            "+    if getattr(args, 'redact_ipv4', False):\n"
            "+        return redact_ipv4(args.text)\n"
        )
        text = (
            "def run(args):\n"
            "    if getattr(args, 'redact_ipv4', False):\n"
            "        return True\n"
            "    return False\n"
        )
        sig = collect_base_signals(["logveil/cli.py"])
        nodes = detect_getattr_shortcuts(
            sig,
            file_contents={"logveil/cli.py": text},
            diff=diff,
        )
        self.assertTrue(nodes)
        self.assertEqual(nodes[0].type, NodeType.GETATTR_SHORTCUT)
        self.assertEqual(nodes[0].risk_tier, Tier.HIGH)
        self.assertEqual(_effective_gate_tier(nodes[0]), Tier.HIGH)
        self.assertIn("redact_ipv4", nodes[0].signals.get("getattr_attrs") or [])

    def test_no_flag_when_attr_not_new(self):
        diff = (
            "diff --git a/logveil/cli.py b/logveil/cli.py\n"
            "--- a/logveil/cli.py\n"
            "+++ b/logveil/cli.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def run(args):\n"
            "+    print(args.verbose)\n"
        )
        text = (
            "def run(args):\n"
            "    if getattr(args, 'verbose', False):\n"
            "        print('v')\n"
        )
        sig = collect_base_signals(["logveil/cli.py"])
        nodes = detect_getattr_shortcuts(
            sig, file_contents={"logveil/cli.py": text}, diff=diff
        )
        self.assertEqual(nodes, [])


class ReflectFixTestsGuidanceTest(unittest.TestCase):
    def test_reflect_mentions_getattr_ban(self):
        record = VerificationRecord(
            command="pytest",
            exit_code=1,
            tests_discovered=3,
            output_excerpt="AttributeError: redact_ipv4",
        )
        msg = _reflect_fix_tests(record, ["logveil/cli.py"])
        self.assertIn("getattr", msg.lower())
        self.assertIn("test helper", msg.lower())


if __name__ == "__main__":
    unittest.main()
