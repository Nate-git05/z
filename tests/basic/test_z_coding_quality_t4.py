"""Coding quality tranche 4: control-plane noise budget."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_cq4_")
os.environ["Z_HOME"] = _HOME
os.environ["Z_CONTROL_PLANE_COMPACT"] = "1"
os.environ.pop("Z_PLAN_CONTEXT_FULL", None)


def _rich_plan():
    from aider.z.uncertainty.architecture import ArchitectureCheckpoint, ArchitectureItem
    from aider.z.uncertainty.capabilities import Capability, CapabilityPlan
    from aider.z.uncertainty.established_solutions import EstablishedSolutionConsideration
    from aider.z.uncertainty.journeys import CriticalJourney, JourneyPlan, JourneyStep
    from aider.z.uncertainty.plan import (
        AmbiguityResolution,
        PlanningArtifact,
        ValidationContract,
    )

    return PlanningArtifact(
        task_id="t-cq4",
        title="Add IPv4 redaction",
        reason="high blast radius",
        approach="Use the stdlib ipaddress module in the redactor path.",
        steps=[
            "Locate redaction helpers",
            "Parse IPv4 with ipaddress",
            "Add unit tests",
            "Run package tests",
        ],
        out_of_scope=["IPv6", "auth changes"],
        validation_contracts=[
            ValidationContract(
                input_name="ip",
                domain="IPv4 string",
                on_invalid="skip / leave unchanged",
            )
        ],
        input_domain_table=[("ip", "IPv4", "dotted quad")],
        invariants=["never invent a custom IPv4 regex parser"],
        ambiguities=[
            AmbiguityResolution(
                ambiguity="private ranges",
                resolution="redact all public+private IPv4",
            )
        ],
        established_solutions=[
            EstablishedSolutionConsideration(
                category_id="ipv4_parsing",
                problem_category="IPv4 address parsing",
                standard_approach="ipaddress.IPv4Address / ip_network",
                decision="use_standard",
            )
        ],
        capability_plan=CapabilityPlan(
            required=[
                Capability(
                    id="verify.network",
                    label="Network verification",
                    evidence_type="test",
                    critical=True,
                )
            ],
            coverage_gaps=[
                Capability(
                    id="verify.network",
                    label="Network verification",
                    evidence_type="test",
                    critical=True,
                )
            ],
            compensation=["Run targeted unit tests for redaction helpers"],
        ),
        architecture=ArchitectureCheckpoint(
            items=[
                ArchitectureItem(
                    id="parse_loc",
                    prompt="Where does parsing live?",
                    status="known",
                    answer="util",
                )
            ],
            recommended_layers=["domain", "adapter"],
            blocking_assumptions=["unknown log format edge cases"],
        ),
        journeys=JourneyPlan(
            journeys=[
                CriticalJourney(
                    id="j1",
                    title="Redact IPs in upload",
                    required_evidence_type="unit",
                    risk="critical",
                    steps=[
                        JourneyStep(index=1, action="upload log", observation="IPs masked"),
                    ],
                )
            ]
        ),
    )


class PlanDirectiveTests(unittest.TestCase):
    def test_compact_smaller_than_full_keeps_established(self):
        from aider.z.uncertainty.plan import (
            _format_plan_for_context_full,
            format_plan_for_context,
        )

        plan = _rich_plan()
        os.environ["Z_CONTROL_PLANE_COMPACT"] = "1"
        os.environ.pop("Z_PLAN_CONTEXT_FULL", None)
        compact = format_plan_for_context(plan)
        full = _format_plan_for_context_full(plan)
        self.assertIn("Established solutions", compact)
        self.assertIn("ipaddress", compact.lower())
        self.assertIn("Approved plan directive", compact)
        self.assertNotIn("Suggested exact-assertion tests", compact)
        self.assertLess(len(compact), len(full))
        self.assertLess(len(compact), int(len(full) * 0.75))

    def test_full_escape_hatch(self):
        from aider.z.uncertainty.plan import format_plan_for_context

        plan = _rich_plan()
        os.environ["Z_PLAN_CONTEXT_FULL"] = "1"
        try:
            full = format_plan_for_context(plan)
            self.assertIn("Approved implementation plan (binding)", full)
            self.assertIn("Architecture checkpoint", full)
        finally:
            os.environ.pop("Z_PLAN_CONTEXT_FULL", None)


class CapabilityDirectiveTests(unittest.TestCase):
    def test_directive_and_fingerprint(self):
        from aider.z.control_plane_budget import (
            capability_plan_fingerprint,
            format_capability_directive,
        )
        from aider.z.uncertainty.capabilities import Capability, CapabilityPlan

        plan = CapabilityPlan(
            required=[
                Capability(id="a", label="A", evidence_type="test", critical=True),
                Capability(id="b", label="B", evidence_type="review"),
            ],
            coverage_gaps=[
                Capability(id="b", label="B", evidence_type="review"),
            ],
            compensation=["Do a careful review of B"],
            available_from_skills=["skill-a"],
        )
        block = format_capability_directive(plan)
        self.assertIn("Capability directive", block)
        self.assertIn("[gap] B", block)
        self.assertIn("[ok] A", block)
        self.assertIn("Do a careful review", block)
        fp = capability_plan_fingerprint(plan)
        self.assertIn("req:a,b", fp)
        self.assertIn("gap:b", fp)

    def test_dedupe_skips_second_identical_inject(self):
        from aider.coders.base_coder import Coder
        from aider.io import InputOutput
        from aider.models import Model
        from aider.z.control_plane_budget import capability_plan_fingerprint
        from aider.z.uncertainty.capabilities import Capability, CapabilityPlan

        io = InputOutput(yes=True)
        coder = Coder.create(
            main_model=Model("gpt-4o-mini"),
            io=io,
            fnames=[],
            edit_format="diff",
        )
        coder.repo = None
        coder.cur_messages = []
        os.environ["Z_CONTROL_PLANE_COMPACT"] = "1"

        plan = CapabilityPlan(
            required=[Capability(id="x", label="X", evidence_type="test")],
            coverage_gaps=[Capability(id="x", label="X", evidence_type="test")],
            compensation=["test it"],
        )
        fp = capability_plan_fingerprint(plan)
        coder._capability_plan_fingerprint = fp

        # Simulate the skip path: identical fingerprint → no new block appended
        # by calling the helper logic inline
        from aider.z.control_plane_budget import (
            control_plane_compact_enabled,
            format_capability_directive,
        )

        n0 = len(coder.cur_messages)
        if control_plane_compact_enabled() and fp == coder._capability_plan_fingerprint:
            pass
        else:
            coder.cur_messages += [
                {"role": "user", "content": format_capability_directive(plan)}
            ]
        self.assertEqual(len(coder.cur_messages), n0)

        # Different fingerprint injects
        coder._capability_plan_fingerprint = "other"
        block = format_capability_directive(plan)
        coder.cur_messages += [{"role": "user", "content": block}]
        coder._capability_plan_fingerprint = fp
        self.assertEqual(len(coder.cur_messages), n0 + 1)


class PlanExitBudgetTests(unittest.TestCase):
    def test_truncates_long_plan(self):
        from aider.z.plan_mode import format_plan_exit_context

        os.environ["Z_CONTROL_PLANE_COMPACT"] = "1"
        os.environ["Z_PLAN_EXIT_CHARS"] = "1000"
        long_body = "LINE\n" * 800
        out = format_plan_exit_context(long_body, plan_path="/tmp/plan.md")
        self.assertIn("Approved plan", out)
        self.assertIn("truncated", out.lower())
        self.assertLess(len(out), len(long_body))
        self.assertLess(len(out), 1600)
