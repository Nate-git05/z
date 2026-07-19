"""Sibling companion / registry trait detection (scrapy-class miss)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="z_sibling_companion_")
os.environ["Z_HOME"] = _HOME

from aider.z.uncertainty.detectors import (  # noqa: E402
    PatternSearchResult,
    detect_missing_sibling_companions,
)
from aider.z.uncertainty.gate import _effective_gate_tier  # noqa: E402
from aider.z.uncertainty.risk import collect_base_signals  # noqa: E402
from aider.z.uncertainty.schema import NodeType, Tier  # noqa: E402
from aider.z.uncertainty.sibling_traits import (  # noqa: E402
    find_sibling_companion_gaps,
    new_files_from_diff,
)


def _write_scrapyish_tree(root: Path) -> None:
    mw = root / "pkg" / "downloadermiddlewares"
    mw.mkdir(parents=True)
    settings = root / "pkg" / "settings"
    settings.mkdir(parents=True)

    (mw / "httperror.py").write_text(
        "class HttpErrorMiddleware:\n    pass\n", encoding="utf-8"
    )
    (mw / "redirect.py").write_text(
        "class RedirectMiddleware:\n    pass\n", encoding="utf-8"
    )
    (mw / "cookies.py").write_text(
        "class CookiesMiddleware:\n    pass\n", encoding="utf-8"
    )
    (mw / "circuitbreaker.py").write_text(
        "class CircuitBreakerMiddleware:\n    pass\n", encoding="utf-8"
    )
    (settings / "default_settings.py").write_text(
        "DOWNLOADER_MIDDLEWARES_BASE = {\n"
        '    "pkg.downloadermiddlewares.httperror.HttpErrorMiddleware": 50,\n'
        '    "pkg.downloadermiddlewares.redirect.RedirectMiddleware": 600,\n'
        '    "pkg.downloadermiddlewares.cookies.CookiesMiddleware": 700,\n'
        "}\n",
        encoding="utf-8",
    )
    (mw / "__init__.py").write_text("# middleware package\n", encoding="utf-8")
    (settings / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")


class SiblingCompanionTest(unittest.TestCase):
    def test_new_files_from_diff(self):
        diff = (
            "diff --git a/pkg/downloadermiddlewares/circuitbreaker.py "
            "b/pkg/downloadermiddlewares/circuitbreaker.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/pkg/downloadermiddlewares/circuitbreaker.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+class CircuitBreakerMiddleware:\n"
            "+    pass\n"
        )
        self.assertEqual(
            new_files_from_diff(diff),
            ["pkg/downloadermiddlewares/circuitbreaker.py"],
        )

    def test_flags_missing_registry_entry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_scrapyish_tree(root)
            new_f = "pkg/downloadermiddlewares/circuitbreaker.py"
            siblings = [
                "pkg/downloadermiddlewares/httperror.py",
                "pkg/downloadermiddlewares/redirect.py",
                "pkg/downloadermiddlewares/cookies.py",
            ]
            gaps = find_sibling_companion_gaps(
                root,
                new_file=new_f,
                sibling_matches=siblings,
                diff=(
                    "diff --git a/pkg/downloadermiddlewares/circuitbreaker.py "
                    "b/pkg/downloadermiddlewares/circuitbreaker.py\n"
                    "new file mode 100644\n"
                    "--- /dev/null\n"
                    "+++ b/pkg/downloadermiddlewares/circuitbreaker.py\n"
                    "@@ -0,0 +1,2 @@\n"
                    "+class CircuitBreakerMiddleware:\n"
                    "+    pass\n"
                ),
                files_changed=[new_f],
            )
            self.assertTrue(gaps, "expected a companion gap for default_settings")
            self.assertTrue(
                any("default_settings" in g.companion_file for g in gaps),
                gaps,
            )
            self.assertEqual(gaps[0].trait_kind, "registry")

    def test_no_gap_when_registry_updated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_scrapyish_tree(root)
            settings = root / "pkg" / "settings" / "default_settings.py"
            settings.write_text(
                "DOWNLOADER_MIDDLEWARES_BASE = {\n"
                '    "pkg.downloadermiddlewares.httperror.HttpErrorMiddleware": 50,\n'
                '    "pkg.downloadermiddlewares.redirect.RedirectMiddleware": 600,\n'
                '    "pkg.downloadermiddlewares.cookies.CookiesMiddleware": 700,\n'
                '    "pkg.downloadermiddlewares.circuitbreaker.CircuitBreakerMiddleware": 90,\n'
                "}\n",
                encoding="utf-8",
            )
            new_f = "pkg/downloadermiddlewares/circuitbreaker.py"
            gaps = find_sibling_companion_gaps(
                root,
                new_file=new_f,
                sibling_matches=[
                    "pkg/downloadermiddlewares/httperror.py",
                    "pkg/downloadermiddlewares/redirect.py",
                    "pkg/downloadermiddlewares/cookies.py",
                ],
                files_changed=[new_f, "pkg/settings/default_settings.py"],
            )
            self.assertEqual(gaps, [])

    def test_detector_emits_medium_gate_node(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_scrapyish_tree(root)
            new_f = "pkg/downloadermiddlewares/circuitbreaker.py"
            sig = collect_base_signals([new_f])
            nodes = detect_missing_sibling_companions(
                sig,
                root=root,
                new_files=[new_f],
                pattern_results={
                    new_f: PatternSearchResult(
                        matches=[
                            "pkg/downloadermiddlewares/httperror.py",
                            "pkg/downloadermiddlewares/redirect.py",
                            "pkg/downloadermiddlewares/cookies.py",
                        ]
                    )
                },
                files_changed=[new_f],
            )
            self.assertTrue(nodes)
            self.assertEqual(nodes[0].type, NodeType.PATTERN_COMPANION_GAP)
            self.assertEqual(nodes[0].risk_tier, Tier.MEDIUM)
            self.assertEqual(_effective_gate_tier(nodes[0]), Tier.MEDIUM)
            self.assertIn("default_settings", nodes[0].signals.get("companion_file", ""))

    def test_dunder_all_companion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "widgets"
            pkg.mkdir()
            (pkg / "alpha.py").write_text("class Alpha: pass\n", encoding="utf-8")
            (pkg / "beta.py").write_text("class Beta: pass\n", encoding="utf-8")
            (pkg / "gamma.py").write_text("class Gamma: pass\n", encoding="utf-8")
            (pkg / "__init__.py").write_text(
                '__all__ = ["Alpha", "Beta", "Gamma"]\n'
                "from .alpha import Alpha\n"
                "from .beta import Beta\n"
                "from .gamma import Gamma\n",
                encoding="utf-8",
            )
            (pkg / "delta.py").write_text("class Delta: pass\n", encoding="utf-8")
            gaps = find_sibling_companion_gaps(
                root,
                new_file="widgets/delta.py",
                sibling_matches=[
                    "widgets/alpha.py",
                    "widgets/beta.py",
                    "widgets/gamma.py",
                ],
                files_changed=["widgets/delta.py"],
            )
            self.assertTrue(gaps)
            self.assertTrue(any(g.companion_file.endswith("__init__.py") for g in gaps))

    def test_shared_base_class_not_flagged_as_missing_registration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plugins = root / "plugins"
            plugins.mkdir()
            (plugins / "base_plugin.py").write_text(
                "class BasePlugin:\n"
                "    def run(self):\n"
                "        raise NotImplementedError\n",
                encoding="utf-8",
            )
            (plugins / "foo_plugin.py").write_text(
                "from plugins.base_plugin import BasePlugin\n\n"
                "class FooPlugin(BasePlugin):\n"
                "    def run(self):\n"
                "        return 'foo'\n",
                encoding="utf-8",
            )
            (plugins / "bar_plugin.py").write_text(
                "from plugins.base_plugin import BasePlugin\n\n"
                "class BarPlugin(BasePlugin):\n"
                "    def run(self):\n"
                "        return 'bar'\n",
                encoding="utf-8",
            )
            (plugins / "baz_plugin.py").write_text(
                "from plugins.base_plugin import BasePlugin\n\n"
                "class BazPlugin(BasePlugin):\n"
                "    def run(self):\n"
                "        return 'baz'\n",
                encoding="utf-8",
            )
            (plugins / "registry.py").write_text(
                "from plugins.foo_plugin import FooPlugin\n"
                "from plugins.bar_plugin import BarPlugin\n"
                "from plugins.baz_plugin import BazPlugin\n\n"
                "PLUGINS = {'foo': FooPlugin, 'bar': BarPlugin, 'baz': BazPlugin}\n",
                encoding="utf-8",
            )
            diff = (
                "diff --git a/plugins/base_plugin.py b/plugins/base_plugin.py\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/plugins/base_plugin.py\n"
                "@@ -0,0 +1,3 @@\n"
                "+class BasePlugin:\n"
                "+    def run(self):\n"
                "+        raise NotImplementedError\n"
            )
            gaps = find_sibling_companion_gaps(
                root,
                new_file="plugins/base_plugin.py",
                sibling_matches=[
                    "plugins/foo_plugin.py",
                    "plugins/bar_plugin.py",
                    "plugins/baz_plugin.py",
                ],
                diff=diff,
            )
            self.assertEqual(gaps, [])

    def test_genuine_new_peer_still_flagged_when_missing_registration(self):
        """Regression: shared-dependency suppress must not disable this family."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plugins = root / "plugins"
            plugins.mkdir()
            (plugins / "foo_plugin.py").write_text(
                "class FooPlugin:\n    def run(self):\n        return 'foo'\n",
                encoding="utf-8",
            )
            (plugins / "bar_plugin.py").write_text(
                "class BarPlugin:\n    def run(self):\n        return 'bar'\n",
                encoding="utf-8",
            )
            (plugins / "baz_plugin.py").write_text(
                "class BazPlugin:\n    def run(self):\n        return 'baz'\n",
                encoding="utf-8",
            )
            (plugins / "registry.py").write_text(
                "from plugins.foo_plugin import FooPlugin\n"
                "from plugins.bar_plugin import BarPlugin\n"
                "from plugins.baz_plugin import BazPlugin\n\n"
                "PLUGINS = {'foo': FooPlugin, 'bar': BarPlugin, 'baz': BazPlugin}\n",
                encoding="utf-8",
            )
            (plugins / "qux_plugin.py").write_text(
                "class QuxPlugin:\n    def run(self):\n        return 'qux'\n",
                encoding="utf-8",
            )
            diff = (
                "diff --git a/plugins/qux_plugin.py b/plugins/qux_plugin.py\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/plugins/qux_plugin.py\n"
                "@@ -0,0 +1,3 @@\n"
                "+class QuxPlugin:\n"
                "+    def run(self):\n"
                "+        return 'qux'\n"
            )
            gaps = find_sibling_companion_gaps(
                root,
                new_file="plugins/qux_plugin.py",
                sibling_matches=[
                    "plugins/foo_plugin.py",
                    "plugins/bar_plugin.py",
                    "plugins/baz_plugin.py",
                ],
                diff=diff,
            )
            self.assertTrue(
                any(g.companion_file == "plugins/registry.py" for g in gaps),
                gaps,
            )


if __name__ == "__main__":
    unittest.main()
