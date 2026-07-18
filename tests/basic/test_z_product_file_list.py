"""Product items that enumerate file lists require full described coverage."""

from __future__ import annotations

import os
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="z_product_files_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.checklist import (  # noqa: E402
    bind_evidence,
    extract_product_file_paths,
    rescore_checklist_with_evidence,
)
from aider.z.uncertainty.schema import RequirementItem, TaskChecklist  # noqa: E402


_ITEM_TEXT = (
    "Add the DebugChannelClient type parameter to createResponse in "
    "packages/react-server-dom-webpack/src/client/ReactFlightDOMClientNode.js, "
    "packages/react-server-dom-turbopack/src/client/ReactFlightDOMClientNode.js, "
    "packages/react-server-dom-parcel/src/client/ReactFlightDOMClientNode.js, "
    "packages/react-server-dom-webpack/src/client/ReactFlightDOMClientEdge.js, "
    "packages/react-server-dom-turbopack/src/client/ReactFlightDOMClientEdge.js, "
    "packages/react-server-dom-parcel/src/client/ReactFlightDOMClientEdge.js, "
    "packages/react-server-dom-esm/src/client/ReactFlightDOMClientNode.js, "
    "packages/react-server-dom-esm/src/client/ReactFlightDOMClientEdge.js, "
    "and packages/react-server-dom-unbundled/src/client/ReactFlightDOMClientNode.js. "
    "Do not skip any of them — an inconsistent subset is worse than not doing this at all."
)

_NODE_WEBPACK = (
    "packages/react-server-dom-webpack/src/client/ReactFlightDOMClientNode.js"
)
_NODE_TURBO = (
    "packages/react-server-dom-turbopack/src/client/ReactFlightDOMClientNode.js"
)
_NODE_PARCEL = (
    "packages/react-server-dom-parcel/src/client/ReactFlightDOMClientNode.js"
)
_NODE_ESM = "packages/react-server-dom-esm/src/client/ReactFlightDOMClientNode.js"
_NODE_UNBUNDLED = (
    "packages/react-server-dom-unbundled/src/client/ReactFlightDOMClientNode.js"
)
_EDGE_WEBPACK = (
    "packages/react-server-dom-webpack/src/client/ReactFlightDOMClientEdge.js"
)
_EDGE_TURBO = (
    "packages/react-server-dom-turbopack/src/client/ReactFlightDOMClientEdge.js"
)
_EDGE_PARCEL = (
    "packages/react-server-dom-parcel/src/client/ReactFlightDOMClientEdge.js"
)
_EDGE_ESM = "packages/react-server-dom-esm/src/client/ReactFlightDOMClientEdge.js"


def _type_update_hunk(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -10,7 +10,7 @@\n"
        "-export function createResponse(stream) {\n"
        "+export function createResponse(stream): DebugChannelClient {\n"
    )


def _unrelated_edge_hunk(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -40,7 +40,7 @@\n"
        "-  startReadingFromStream(reader, stream, sink);\n"
        "+  startReadingFromStream(reader, stream, sink, debugValue);\n"
    )


class ExtractProductFilePathsTest(unittest.TestCase):
    def test_extracts_nine_flight_paths(self):
        paths = extract_product_file_paths(_ITEM_TEXT)
        self.assertGreaterEqual(len(paths), 9, paths)
        joined = " ".join(paths)
        self.assertIn("ReactFlightDOMClientNode.js", joined)
        self.assertIn("ReactFlightDOMClientEdge.js", joined)
        self.assertIn("webpack", joined)
        self.assertIn("turbopack", joined)


class ProductFileListEvidenceTest(unittest.TestCase):
    def _item(self):
        return RequirementItem(text=_ITEM_TEXT, kind="product")

    def test_partial_subset_is_not_fully_addressed(self):
        """5/9 Node files with the real type update; Edge untouched → Partial."""
        node_paths = [
            _NODE_WEBPACK,
            _NODE_TURBO,
            _NODE_PARCEL,
            _NODE_ESM,
            _NODE_UNBUNDLED,
        ]
        diff = "".join(_type_update_hunk(p) for p in node_paths)
        item = self._item()
        cl = TaskChecklist(task_id="t", title="flight", items=[item])
        evidence = bind_evidence(
            cl,
            files_changed=node_paths,
            file_contents={
                p: "export function createResponse(stream): DebugChannelClient {}\n"
                for p in node_paths
            },
            symbols=["createResponse", "DebugChannelClient"],
            test_files=["packages/react-server-dom-webpack/src/__tests__/foo.js"],
            last_diff=diff,
        )
        rescore_checklist_with_evidence(cl, evidence)
        self.assertEqual(item.status, "Partially Addressed")
        self.assertIn("files_list_incomplete:true", evidence[0].evidence_notes)
        self.assertTrue(
            any(str(n).startswith("files_missing:") for n in evidence[0].evidence_notes),
            evidence[0].evidence_notes,
        )
        self.assertIn("ReactFlightDOMClientEdge.js", evidence[0].missing)

    def test_unrelated_edge_touch_does_not_count(self):
        """Edge files edited for a different bug must not satisfy the type update."""
        node_paths = [_NODE_WEBPACK, _NODE_TURBO]
        edge_paths = [_EDGE_WEBPACK, _EDGE_TURBO, _EDGE_PARCEL, _EDGE_ESM]
        diff = "".join(_type_update_hunk(p) for p in node_paths) + "".join(
            _unrelated_edge_hunk(p) for p in edge_paths
        )
        changed = node_paths + edge_paths
        item = self._item()
        cl = TaskChecklist(task_id="t", title="flight", items=[item])
        evidence = bind_evidence(
            cl,
            files_changed=changed,
            symbols=["createResponse", "DebugChannelClient", "startReadingFromStream"],
            test_files=["packages/react-server-dom-webpack/src/__tests__/foo.js"],
            last_diff=diff,
        )
        rescore_checklist_with_evidence(cl, evidence)
        self.assertNotEqual(item.status, "Fully Addressed")
        missing_note = next(
            n for n in evidence[0].evidence_notes if str(n).startswith("files_missing:")
        )
        self.assertIn("ReactFlightDOMClientEdge.js", missing_note)
        # Unrelated Edge touch must not remove Edge from missing
        self.assertIn(_EDGE_WEBPACK.split("/")[-1], missing_note)

    def test_full_coverage_with_described_change_clears_file_list(self):
        all_paths = [
            _NODE_WEBPACK,
            _NODE_TURBO,
            _NODE_PARCEL,
            _NODE_ESM,
            _NODE_UNBUNDLED,
            _EDGE_WEBPACK,
            _EDGE_TURBO,
            _EDGE_PARCEL,
            _EDGE_ESM,
        ]
        # Item text names 9 paths; include a 9th Node unbundled already in list
        diff = "".join(_type_update_hunk(p) for p in all_paths)
        item = self._item()
        cl = TaskChecklist(task_id="t", title="flight", items=[item])
        evidence = bind_evidence(
            cl,
            files_changed=all_paths,
            symbols=["createResponse", "DebugChannelClient"],
            test_files=["packages/react-server-dom-webpack/src/__tests__/foo.js"],
            last_diff=diff,
        )
        self.assertIn("files_list_complete:true", evidence[0].evidence_notes)
        self.assertFalse(
            any(str(n).startswith("files_missing:") for n in evidence[0].evidence_notes)
        )
        rescore_checklist_with_evidence(cl, evidence)
        self.assertEqual(item.status, "Fully Addressed")


if __name__ == "__main__":
    unittest.main()
