"""Established-solutions taxonomy + gated-plan question + post-diff detector."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_established_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.detectors import detect_established_solution_gaps  # noqa: E402
from aider.z.uncertainty.established_solutions import (  # noqa: E402
    ESTABLISHED_SOLUTIONS,
    EstablishedSolutionConsideration,
    match_request_categories,
    scan_invention_in_diff,
    taxonomy_category_ids,
)
from aider.z.uncertainty.gate import _effective_gate_tier  # noqa: E402
from aider.z.uncertainty.plan import (  # noqa: E402
    draft_plan_from_request,
    format_plan_for_context,
    format_plan_for_user,
    triage_for_planning,
)
from aider.z.uncertainty.risk import collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import NodeType, Tier  # noqa: E402


class EstablishedTaxonomyTest(unittest.TestCase):
    def test_taxonomy_covers_core_categories(self):
        ids = set(taxonomy_category_ids())
        for needed in (
            "ipv4_parsing",
            "email_parsing",
            "url_parsing",
            "datetime_parsing",
            "uuid_parsing",
        ):
            self.assertIn(needed, ids)
        self.assertGreaterEqual(len(ESTABLISHED_SOLUTIONS), 5)

    def test_request_matches_ipv4_redaction(self):
        cats = match_request_categories(
            "Add opt-in IPv4 redaction for IP addresses in logs"
        )
        self.assertTrue(any(c.category_id == "ipv4_parsing" for c in cats))

    def test_scan_flags_hand_rolled_ipv4_regex(self):
        diff = (
            "diff --git a/logveil/redact.py b/logveil/redact.py\n"
            "--- a/logveil/redact.py\n"
            "+++ b/logveil/redact.py\n"
            "@@ -1,2 +1,6 @@\n"
            "+import re\n"
            "+_IPV4 = re.compile(\n"
            '+    r"(?<!\\d)(?:\\d{1,3}\\.){3}\\d{1,3}(?!\\d)"\n'
            "+)\n"
            "+def redact_ipv4(text): return _IPV4.sub('x', text)\n"
        )
        hits = scan_invention_in_diff(diff)
        self.assertTrue(any(h.category_id == "ipv4_parsing" for h in hits), hits)

    def test_scan_quiet_when_ipaddress_used(self):
        diff = (
            "diff --git a/logveil/redact.py b/logveil/redact.py\n"
            "--- a/logveil/redact.py\n"
            "+++ b/logveil/redact.py\n"
            "@@ -1,2 +1,8 @@\n"
            "+import ipaddress\n"
            "+import re\n"
            "+_CANDIDATE = re.compile(r'(?:\\d{1,3}\\.){3}\\d{1,3}')\n"
            "+def redact_ipv4(text):\n"
            "+    def ok(m):\n"
            "+        try: ipaddress.ip_address(m.group(0)); return True\n"
            "+        except ValueError: return False\n"
        )
        hits = scan_invention_in_diff(diff)
        self.assertFalse(any(h.category_id == "ipv4_parsing" for h in hits), hits)

    def test_docstring_mention_does_not_suppress_lru_invention(self):
        """Lucky wording in a docstring must not count as using the standard."""
        diff = (
            "diff --git a/cache.py b/cache.py\n"
            "--- a/cache.py\n"
            "+++ b/cache.py\n"
            "@@ -0,0 +1,6 @@\n"
            "+class SimpleLRUCache:\n"
            '+    """avoids functools.lru_cache because we need eviction hooks"""\n'
            "+    def __init__(self):\n"
            "+        self._data = {}\n"
        )
        hits = scan_invention_in_diff(diff)
        self.assertTrue(
            any(h.category_id == "lru_cache" for h in hits),
            hits,
        )

    def test_hash_comment_already_ignored_for_suppression(self):
        """# comments are stripped in _added_lines — not the docstring path."""
        diff = (
            "diff --git a/cache.py b/cache.py\n"
            "--- a/cache.py\n"
            "+++ b/cache.py\n"
            "@@ -0,0 +1,5 @@\n"
            "+# avoids functools.lru_cache because we need eviction hooks\n"
            "+class SimpleLRUCache:\n"
            "+    def __init__(self):\n"
            "+        self._data = {}\n"
        )
        hits = scan_invention_in_diff(diff)
        self.assertTrue(any(h.category_id == "lru_cache" for h in hits), hits)

    def test_invention_still_sees_regex_string_contents(self):
        """String stripping must not apply to the invention check."""
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+import re\n"
            '+_IPV4 = re.compile(r"\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}")\n'
        )
        hits = scan_invention_in_diff(diff)
        self.assertTrue(any(h.category_id == "ipv4_parsing" for h in hits), hits)

    def test_sibling_real_lru_usage_suppresses_via_repo_search(self):
        """Untouched sibling with real @lru_cache must suppress (widened Fix 1)."""
        root = Path(tempfile.mkdtemp(prefix="z_est_sib_"))
        (root / "helpers.py").write_text(
            "from functools import lru_cache\n"
            "@lru_cache(maxsize=128)\n"
            "def expensive(x):\n"
            "    return x * 2\n",
            encoding="utf-8",
        )
        (root / "cache.py").write_text(
            "class SimpleLRUCache:\n"
            "    def __init__(self):\n"
            "        self._data = {}\n",
            encoding="utf-8",
        )
        diff = (
            "diff --git a/cache.py b/cache.py\n"
            "--- a/cache.py\n"
            "+++ b/cache.py\n"
            "@@ -0,0 +1,4 @@\n"
            "+class SimpleLRUCache:\n"
            "+    def __init__(self):\n"
            "+        self._data = {}\n"
        )
        # Without root → would flag; with root → sibling suppresses
        alone = scan_invention_in_diff(diff)
        self.assertTrue(any(h.category_id == "lru_cache" for h in alone), alone)
        quiet = scan_invention_in_diff(
            diff,
            root=root,
            focus_files=["cache.py"],
            file_contents={
                "cache.py": (root / "cache.py").read_text(encoding="utf-8"),
            },
        )
        self.assertFalse(
            any(h.category_id == "lru_cache" for h in quiet),
            quiet,
        )

    def test_semver_regex_not_flagged_as_ipv4(self):
        diff = '+VERSION_RE = re.compile(r"(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})")\n'
        hits = scan_invention_in_diff(diff)
        self.assertFalse(
            any(h.category_id == "ipv4_parsing" for h in hits),
            hits,
        )

    def test_ssh_target_regex_not_flagged_as_email(self):
        diff = '+TARGET_RE = re.compile(r"(\\w+)@([\\w.-]+)")\n'
        hits = scan_invention_in_diff(diff)
        self.assertFalse(
            any(h.category_id == "email_parsing" for h in hits),
            hits,
        )

    def test_genuine_ipv4_invention_still_flagged(self):
        diff = (
            "+def validate_ip(addr):\n"
            '+    return re.match(r"(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})", addr)\n'
        )
        hits = scan_invention_in_diff(diff)
        self.assertTrue(
            any(h.category_id == "ipv4_parsing" for h in hits),
            hits,
        )

    def test_genuine_email_invention_still_flagged(self):
        diff = (
            "+def validate_email(addr):\n"
            '+    return re.match(r"[\\w.]+@[\\w.]+", addr)\n'
        )
        hits = scan_invention_in_diff(diff)
        self.assertTrue(
            any(h.category_id == "email_parsing" for h in hits),
            hits,
        )


class EstablishedPlanningTest(unittest.TestCase):
    def test_ipv4_request_triggers_gated_plan(self):
        required, reason, _ = triage_for_planning(
            ["logveil/redact.py"],
            user_text="Add opt-in IPv4 redaction for dotted IP addresses.",
        )
        self.assertTrue(required)
        self.assertIn("established_solution", reason)
        self.assertIn("ipv4_parsing", reason)

    def test_plan_includes_established_solutions_section(self):
        plan = draft_plan_from_request(
            "Add opt-in IPv4 redaction.",
            title="IPv4 redaction",
            reason="established_solution:ipv4_parsing",
            files=["logveil/redact.py"],
        )
        self.assertTrue(plan.established_solutions)
        self.assertTrue(
            any(e.category_id == "ipv4_parsing" for e in plan.established_solutions)
        )
        user = format_plan_for_user(plan)
        self.assertIn("Established solutions", user)
        self.assertIn("ipv4_parsing", user)
        ctx = format_plan_for_context(plan)
        self.assertIn("Established solutions", ctx)
        self.assertIn("ipaddress", ctx.lower())


class EstablishedDetectorTest(unittest.TestCase):
    def test_detector_flags_without_plan_coverage(self):
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+import re\n"
            '+_IPV4 = re.compile(r"\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}")\n'
        )
        sig = collect_base_signals(["x.py"])
        nodes = detect_established_solution_gaps(sig, diff=diff, plan=None)
        self.assertTrue(nodes)
        self.assertEqual(nodes[0].type, NodeType.ESTABLISHED_SOLUTION_GAP)
        self.assertEqual(_effective_gate_tier(nodes[0]), Tier.MEDIUM)

    def test_use_standard_in_plan_still_flags_if_diff_invents(self):
        """Plan said use_standard but diff still hand-rolls — keep the flag."""
        from aider.z.uncertainty.plan import PlanningArtifact

        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -0,0 +1,2 @@\n"
            '+_IPV4 = re.compile(r"\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}")\n'
        )
        plan = PlanningArtifact(
            task_id="t1",
            title="IPv4",
            established_solutions=[
                EstablishedSolutionConsideration(
                    category_id="ipv4_parsing",
                    problem_category="IPv4 address parsing / validation",
                    standard_approach="Python: ipaddress.ip_address",
                    decision="use_standard",
                )
            ],
            approved=True,
        )
        sig = collect_base_signals(["x.py"])
        nodes = detect_established_solution_gaps(sig, diff=diff, plan=plan)
        self.assertTrue(nodes)

    def test_custom_justification_suppresses(self):
        from aider.z.uncertainty.plan import PlanningArtifact

        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -0,0 +1,2 @@\n"
            '+_IPV4 = re.compile(r"\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}")\n'
        )
        plan = PlanningArtifact(
            task_id="t1",
            title="IPv4",
            established_solutions=[
                EstablishedSolutionConsideration(
                    category_id="ipv4_parsing",
                    problem_category="IPv4",
                    decision="custom",
                    custom_justification=(
                        "Must run without importing ipaddress in a tiny WASM build."
                    ),
                )
            ],
            approved=True,
        )
        sig = collect_base_signals(["x.py"])
        nodes = detect_established_solution_gaps(sig, diff=diff, plan=plan)
        self.assertEqual(nodes, [])


if __name__ == "__main__":
    unittest.main()
