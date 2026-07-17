"""Investigative hints are trackable checklist obligations (fmtlog #115 lesson)."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_investigate_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.checklist import (  # noqa: E402
    ItemEvidence,
    bind_evidence,
    classify_requirement_kind,
    decompose_request,
    extract_investigation_clauses,
    extract_investigation_targets,
    rescore_checklist_with_evidence,
)
from aider.z.uncertainty.detectors import detect_requirement_gaps  # noqa: E402
from aider.z.uncertainty.evidence_strategy import (  # noqa: E402
    ALL_REQUIREMENT_KINDS,
    KIND_VERIFIERS,
    combine_model_and_mechanical,
    hard_block_kind,
    status_from_strategy,
)
from aider.z.uncertainty.gate import _effective_gate_tier  # noqa: E402
from aider.z.uncertainty.risk import collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import (  # noqa: E402
    RequirementItem,
    TaskChecklist,
    Tier,
)


_FMTLOG_HINT = (
    "Re-fix the polling-thread segfault. Also investigate a "
    "vector-reallocation race in bgLogInfos / logInfos via unguarded "
    "header->logId indexing while registerLogInfo() could concurrently grow "
    "the vector."
)


class ClassifyAndDecomposeTest(unittest.TestCase):
    def test_investigation_in_registry(self):
        self.assertIn("investigation", ALL_REQUIREMENT_KINDS)
        self.assertIn("investigation", KIND_VERIFIERS)
        self.assertTrue(hard_block_kind("investigation"))

    def test_classifies_investigate_clause(self):
        self.assertEqual(
            classify_requirement_kind(
                "Also investigate a vector-reallocation race in bgLogInfos"
            ),
            "investigation",
        )

    def test_extracts_named_targets(self):
        targets = extract_investigation_targets(
            "race in bgLogInfos / logInfos while registerLogInfo grows"
        )
        joined = " ".join(targets)
        self.assertIn("bgLogInfos", joined)
        self.assertIn("registerLogInfo", joined)

    def test_decompose_lifts_embedded_investigation(self):
        cl = decompose_request("fmtlog #115", _FMTLOG_HINT)
        kinds = {i.kind for i in cl.items}
        self.assertIn("investigation", kinds)
        inv = [i for i in cl.items if i.kind == "investigation"]
        self.assertTrue(inv)
        blob = " ".join(i.text for i in inv).lower()
        self.assertTrue(
            "bgloginfos" in blob or "registerloginfo" in blob or "reallocation" in blob,
            inv,
        )

    def test_clauses_from_paragraph(self):
        clauses = extract_investigation_clauses(_FMTLOG_HINT)
        self.assertTrue(clauses)


class DispositionEvidenceTest(unittest.TestCase):
    def _item(self):
        return RequirementItem(
            text=(
                "Also investigate a vector-reallocation race in bgLogInfos / "
                "registerLogInfo"
            ),
            kind="investigation",
        )

    def test_not_checked_hard_blocks(self):
        item = self._item()
        cl = TaskChecklist(task_id="t", title="fmtlog", items=[item])
        evidence = bind_evidence(
            cl,
            files_changed=["src/SPSCQueue.h"],
            file_contents={
                "src/SPSCQueue.h": "std::atomic<uint32_t> read_idx;\n"
            },
            symbols=["read_idx"],
            execution_log="",
            last_diff=(
                "diff --git a/src/SPSCQueue.h b/src/SPSCQueue.h\n"
                "+    std::atomic<uint32_t> read_idx;\n"
            ),
        )
        rescore_checklist_with_evidence(cl, evidence)
        self.assertEqual(item.status, "Not Addressed")
        self.assertIn("disposition:not_checked", evidence[0].evidence_notes)

        sig = collect_base_signals(["src/SPSCQueue.h"])
        nodes = detect_requirement_gaps(sig, checklist=cl, gap_details=[])
        inv_nodes = [
            n
            for n in nodes
            if n.signals.get("requirement_kind") == "investigation"
        ]
        self.assertTrue(inv_nodes)
        self.assertEqual(inv_nodes[0].risk_tier, Tier.HIGH)
        self.assertEqual(_effective_gate_tier(inv_nodes[0]), Tier.HIGH)

    def test_checked_fixed_via_diff_touch(self):
        item = self._item()
        cl = TaskChecklist(task_id="t", title="fmtlog", items=[item])
        evidence = bind_evidence(
            cl,
            files_changed=["src/fmtlog.cpp"],
            file_contents={
                "src/fmtlog.cpp": "void registerLogInfo() { bgLogInfos.emplace_back(); }\n"
            },
            symbols=["registerLogInfo", "bgLogInfos"],
            last_diff=(
                "diff --git a/src/fmtlog.cpp b/src/fmtlog.cpp\n"
                "+void registerLogInfo() {\n"
                "+  std::lock_guard<std::mutex> g(mu);\n"
                "+  bgLogInfos.emplace_back();\n"
                "+}\n"
            ),
        )
        rescore_checklist_with_evidence(cl, evidence)
        self.assertEqual(item.status, "Fully Addressed")
        self.assertIn("disposition:checked_fixed", evidence[0].evidence_notes)

    def test_checked_ruled_out_via_inspect_log(self):
        item = self._item()
        cl = TaskChecklist(task_id="t", title="fmtlog", items=[item])
        evidence = bind_evidence(
            cl,
            files_changed=["src/SPSCQueue.h"],
            file_contents={"src/SPSCQueue.h": "atomic size;\n"},
            symbols=["size"],
            execution_log=(
                "inspect: read src/fmtlog.cpp\n"
                "grep: rg bgLogInfos registerLogInfo src/\n"
                "bgLogInfos looks single-threaded at register time — ruled out\n"
            ),
            last_diff="+ atomic size;\n",
        )
        rescore_checklist_with_evidence(cl, evidence)
        self.assertEqual(item.status, "Fully Addressed")
        self.assertIn("disposition:checked_ruled_out", evidence[0].evidence_notes)

    def test_model_cannot_raise_not_to_fully(self):
        ev = ItemEvidence(
            item_id="1",
            item_text="Investigate bgLogInfos",
            kind="investigation",
            evidence_notes=["disposition:not_checked", "targets:bgLogInfos"],
        )
        self.assertEqual(status_from_strategy(ev), "Not Addressed")
        final, ceilinged = combine_model_and_mechanical(
            "Not Addressed", "Fully Addressed", ev=ev
        )
        self.assertEqual(final, "Not Addressed")
        self.assertTrue(ceilinged)


if __name__ == "__main__":
    unittest.main()
