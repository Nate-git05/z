#!/usr/bin/env python3
"""Generate P2 fixtures + 30 benchmark issue JSON files.

Run from repo root: ``python scripts/generate_p2_benchmark.py``
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P2 = ROOT / "benchmarks" / "p2"
FIX = P2 / "fixtures"
ISSUES = P2 / "issues"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def gen_fixture_calc() -> None:
    base = FIX / "calc"
    write(
        base / "calcpkg" / "__init__.py",
        '"""Tiny calculator package for P2 fixtures."""\n\nfrom .ops import add, average, divide\n\n__all__ = ["add", "average", "divide"]\n',
    )
    # Buggy average: uses len-1 (off-by-one) — ground truth for diagnosis/bugfix
    write(
        base / "calcpkg" / "ops.py",
        '''"""Arithmetic helpers."""


def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("b must be non-zero")
    return a / b


def average(nums):
    """Return the arithmetic mean of nums.

    BUG: divides by len(nums) - 1 when len > 1 (off-by-one).
    """
    if not nums:
        raise ValueError("nums must be non-empty")
    if len(nums) == 1:
        return float(nums[0])
    return sum(nums) / (len(nums) - 1)
''',
    )
    write(
        base / "tests" / "test_visible.py",
        '''import unittest
from calcpkg import add, divide


class VisibleTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_divide(self):
        self.assertEqual(divide(10, 2), 5.0)


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(
        base / "hidden_tests" / "test_average.py",
        '''import unittest
from calcpkg import average


class HiddenAverageTests(unittest.TestCase):
    def test_average_two(self):
        self.assertEqual(average([2, 4]), 3.0)

    def test_average_three(self):
        self.assertAlmostEqual(average([1, 2, 3]), 2.0)


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(base / "PINNED_REF", "calc@v1\n")
    write(base / "README.md", "# calc fixture (pinned calc@v1)\n")


def gen_fixture_inventory() -> None:
    base = FIX / "inventory"
    write(
        base / "invpkg" / "__init__.py",
        "from .stock import Stock\n\n__all__ = ['Stock']\n",
    )
    write(
        base / "invpkg" / "stock.py",
        '''"""Inventory stock tracker."""


class Stock:
    def __init__(self, sku: str, qty: int = 0):
        self.sku = sku
        self.qty = qty

    def receive(self, n: int) -> None:
        if n < 0:
            raise ValueError("n must be >= 0")
        self.qty += n

    def ship(self, n: int) -> None:
        """Ship n units.

        BUG: allows shipping one more than available (uses > instead of >=).
        """
        if n < 0:
            raise ValueError("n must be >= 0")
        if n > self.qty + 1:  # buggy threshold
            raise ValueError("insufficient stock")
        self.qty -= n
''',
    )
    write(
        base / "tests" / "test_visible.py",
        '''import unittest
from invpkg import Stock


class VisibleTests(unittest.TestCase):
    def test_receive(self):
        s = Stock("a", 0)
        s.receive(5)
        self.assertEqual(s.qty, 5)


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(
        base / "hidden_tests" / "test_ship.py",
        '''import unittest
from invpkg import Stock


class HiddenShipTests(unittest.TestCase):
    def test_cannot_overship(self):
        s = Stock("a", 3)
        with self.assertRaises(ValueError):
            s.ship(4)
        self.assertEqual(s.qty, 3)

    def test_exact_ship(self):
        s = Stock("a", 3)
        s.ship(3)
        self.assertEqual(s.qty, 0)


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(base / "PINNED_REF", "inventory@v1\n")


def gen_fixture_greeter() -> None:
    base = FIX / "greeter"
    write(
        base / "greetpkg" / "__init__.py",
        "from .hello import greet\n\n__all__ = ['greet']\n",
    )
    write(
        base / "greetpkg" / "hello.py",
        '''"""Greeting helpers — feature work targets this module."""


def greet(name: str) -> str:
    return f"Hello, {name}!"
''',
    )
    write(
        base / "tests" / "test_visible.py",
        '''import unittest
from greetpkg import greet


class VisibleTests(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet("Ada"), "Hello, Ada!")


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(
        base / "hidden_tests" / "test_shout.py",
        '''import unittest
from greetpkg.hello import shout


class HiddenShoutTests(unittest.TestCase):
    def test_shout(self):
        self.assertEqual(shout("ada"), "ADA!")


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(base / "PINNED_REF", "greeter@v1\n")


def gen_fixture_configpkg() -> None:
    base = FIX / "configpkg"
    write(
        base / "cfg" / "__init__.py",
        "from .settings import Config\n\n__all__ = ['Config']\n",
    )
    write(
        base / "cfg" / "settings.py",
        '''"""Config API — migration renames get_value → get."""


class Config:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get_value(self, key, default=None):
        """Legacy API — callers must migrate to get()."""
        return self._data.get(key, default)

    def set_value(self, key, value):
        self._data[key] = value
''',
    )
    write(
        base / "cfg" / "app.py",
        '''from .settings import Config


def load_port():
    c = Config({"port": 8080})
    return c.get_value("port")


def load_host():
    c = Config({"host": "localhost"})
    return c.get_value("host", "127.0.0.1")
''',
    )
    write(
        base / "tests" / "test_visible.py",
        '''import unittest
from cfg import Config


class VisibleTests(unittest.TestCase):
    def test_get_value(self):
        c = Config({"a": 1})
        self.assertEqual(c.get_value("a"), 1)


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(
        base / "hidden_tests" / "test_migrated.py",
        '''import unittest
from cfg.settings import Config
from cfg import app


class HiddenMigrationTests(unittest.TestCase):
    def test_get_method_exists(self):
        c = Config({"a": 1})
        self.assertTrue(hasattr(c, "get"))
        self.assertEqual(c.get("a"), 1)

    def test_call_sites_migrated(self):
        self.assertEqual(app.load_port(), 8080)
        self.assertEqual(app.load_host(), "localhost")
        # ensure legacy name unused in app.py
        import inspect
        src = inspect.getsource(app)
        self.assertNotIn("get_value", src)


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(base / "PINNED_REF", "configpkg@v1\n")


def gen_fixture_sorter() -> None:
    base = FIX / "sorter"
    write(
        base / "sortpkg" / "__init__.py",
        "from .bubble import sort_list\n\n__all__ = ['sort_list']\n",
    )
    write(
        base / "sortpkg" / "bubble.py",
        '''"""Inline bubble sort — refactor should extract swap helper without behavior change."""


def sort_list(items):
    xs = list(items)
    n = len(xs)
    for i in range(n):
        for j in range(0, n - i - 1):
            if xs[j] > xs[j + 1]:
                tmp = xs[j]
                xs[j] = xs[j + 1]
                xs[j + 1] = tmp
    return xs
''',
    )
    write(
        base / "tests" / "test_visible.py",
        '''import unittest
from sortpkg import sort_list


class VisibleTests(unittest.TestCase):
    def test_sort(self):
        self.assertEqual(sort_list([3, 1, 2]), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(
        base / "hidden_tests" / "test_refactor.py",
        '''import unittest
import inspect
from sortpkg import sort_list
from sortpkg import bubble


class HiddenRefactorTests(unittest.TestCase):
    def test_behavior_unchanged(self):
        self.assertEqual(sort_list([5, 4, 3]), [3, 4, 5])
        self.assertEqual(sort_list([]), [])

    def test_swap_helper_exists(self):
        self.assertTrue(hasattr(bubble, "swap"))
        src = inspect.getsource(bubble.sort_list)
        self.assertIn("swap", src)


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(base / "PINNED_REF", "sorter@v1\n")


def gen_fixture_reviewlib() -> None:
    base = FIX / "reviewlib"
    write(
        base / "revpkg" / "__init__.py",
        "from .parse import parse_ints\n\n__all__ = ['parse_ints']\n",
    )
    write(
        base / "revpkg" / "parse.py",
        '''"""Intentionally fragile parser for review-only tasks."""


def parse_ints(text: str):
    # Broad except absorbs failures — review should flag this.
    try:
        return [int(x) for x in text.split(",")]
    except Exception:
        return []
''',
    )
    write(
        base / "tests" / "test_visible.py",
        '''import unittest
from revpkg import parse_ints


class VisibleTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(parse_ints("1,2,3"), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(
        base / "hidden_tests" / "test_review_artifact.py",
        '''import unittest
from pathlib import Path


class HiddenReviewArtifact(unittest.TestCase):
    def test_review_notes_exist(self):
        # Harness/scripted agent may write REVIEW_NOTES.md; if absent, still ok
        # for review-only (scored primarily on no-edits + findings in result).
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(base / "PINNED_REF", "reviewlib@v1\n")


# Fixed solutions for scripted agent
FIXED_AVERAGE = '''"""Arithmetic helpers."""


def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("b must be non-zero")
    return a / b


def average(nums):
    """Return the arithmetic mean of nums."""
    if not nums:
        raise ValueError("nums must be non-empty")
    return sum(nums) / len(nums)
'''

FIXED_STOCK = '''"""Inventory stock tracker."""


class Stock:
    def __init__(self, sku: str, qty: int = 0):
        self.sku = sku
        self.qty = qty

    def receive(self, n: int) -> None:
        if n < 0:
            raise ValueError("n must be >= 0")
        self.qty += n

    def ship(self, n: int) -> None:
        """Ship n units."""
        if n < 0:
            raise ValueError("n must be >= 0")
        if n > self.qty:
            raise ValueError("insufficient stock")
        self.qty -= n
'''

FEATURE_HELLO = '''"""Greeting helpers — feature work targets this module."""


def greet(name: str) -> str:
    return f"Hello, {name}!"


def shout(name: str) -> str:
    return f"{name.upper()}!"
'''

MIGRATED_SETTINGS = '''"""Config API — get() is the supported accessor."""


class Config:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    # Keep alias briefly for external callers outside this fixture package
    get_value = get

    def set_value(self, key, value):
        self._data[key] = value
'''

MIGRATED_APP = '''from .settings import Config


def load_port():
    c = Config({"port": 8080})
    return c.get("port")


def load_host():
    c = Config({"host": "localhost"})
    return c.get("host", "127.0.0.1")
'''

REFAC_BUBBLE = '''"""Bubble sort with extracted swap helper — behavior unchanged."""


def swap(xs, i, j):
    xs[i], xs[j] = xs[j], xs[i]


def sort_list(items):
    xs = list(items)
    n = len(xs)
    for i in range(n):
        for j in range(0, n - i - 1):
            if xs[j] > xs[j + 1]:
                swap(xs, j, j + 1)
    return xs
'''


def issue(**kwargs):
    return kwargs


def gen_issues() -> None:
    ISSUES.mkdir(parents=True, exist_ok=True)
    items = []

    # --- Diagnosis (5) ---
    items.append(
        issue(
            id="p2-001-diagnosis-average-api-trap",
            task_type="diagnosis",
            fixture_repo="calc",
            task_prompt=(
                "Investigate why average([2,4]) returns 4.0 instead of 3.0 in calcpkg.ops. "
                "The report mentions the API and UI auth pages only as context from the "
                "ticket template — do not treat those as the problem. Do not edit anything."
            ),
            hidden_tests=[],
            expected_edit_scope="none",
            ground_truth_root_cause=(
                "average divides by len(nums)-1 off-by-one when length > 1"
            ),
            known_traps=["P0.2/P0.3 negative controls: incidental API/UI/auth words"],
            intended_clauses=[
                {"kind": "investigation_target", "text": "why average returns wrong", "evidence_source": "code"},
                {"kind": "constraint", "text": "do not edit anything"},
                {"kind": "observation", "text": "average([2,4]) returns 4.0"},
            ],
            scripted={
                "root_cause_statement": (
                    "average divides by len(nums)-1 off-by-one when length > 1"
                ),
                "shell_commands": ["git status", "rg average calcpkg"],
                "claim_complete": True,
            },
            notes="Trap: API/UI/auth incidental",
        )
    )
    items.append(
        issue(
            id="p2-002-diagnosis-stock-overship",
            task_type="diagnosis",
            fixture_repo="inventory",
            task_prompt=(
                "Determine why Stock.ship allows qty to go negative when shipping "
                "exactly qty+1. Do not change the inventory API."
            ),
            hidden_tests=[],
            expected_edit_scope="none",
            ground_truth_root_cause=(
                "ship uses buggy threshold n > qty+1 allowing one extra unit"
            ),
            known_traps=["P0.2 do-not-change-API constraint"],
            intended_clauses=[
                {"kind": "investigation_target", "text": "why ship allows negative", "evidence_source": "code"},
                {"kind": "constraint", "text": "Do not change the inventory API"},
            ],
            scripted={
                "root_cause_statement": (
                    "ship uses buggy threshold n > qty+1 allowing one extra unit"
                ),
                "shell_commands": ["rg ship invpkg"],
                "claim_complete": True,
            },
        )
    )
    items.append(
        issue(
            id="p2-003-diagnosis-divide-zero",
            task_type="diagnosis",
            fixture_repo="calc",
            task_prompt=(
                "Explain how divide handles b=0. Read-only — do not implement a fix."
            ),
            hidden_tests=[],
            expected_edit_scope="none",
            ground_truth_root_cause=(
                "divide raises ZeroDivisionError when b is zero"
            ),
            known_traps=[],
            intended_clauses=[
                {"kind": "investigation_target", "text": "how divide handles b=0", "evidence_source": "code"},
                {"kind": "constraint", "text": "do not implement a fix"},
            ],
            scripted={
                "root_cause_statement": "divide raises ZeroDivisionError when b is zero",
                "shell_commands": ["rg ZeroDivisionError calcpkg"],
                "claim_complete": True,
            },
        )
    )
    items.append(
        issue(
            id="p2-004-diagnosis-config-legacy",
            task_type="diagnosis",
            fixture_repo="configpkg",
            task_prompt=(
                "Investigate which Config method is the legacy accessor still used by app.py. "
                "Do not edit. Auth is out of scope."
            ),
            hidden_tests=[],
            expected_edit_scope="none",
            ground_truth_root_cause=(
                "app.py still calls legacy Config.get_value accessor"
            ),
            known_traps=["P0.3 auth incidental out of scope"],
            intended_clauses=[
                {"kind": "investigation_target", "text": "legacy accessor in app.py", "evidence_source": "code"},
                {"kind": "constraint", "text": "Do not edit"},
            ],
            scripted={
                "root_cause_statement": "app.py still calls legacy Config.get_value accessor",
                "shell_commands": ["rg get_value cfg"],
                "claim_complete": True,
            },
        )
    )
    items.append(
        issue(
            id="p2-005-diagnosis-parse-except",
            task_type="diagnosis",
            fixture_repo="reviewlib",
            task_prompt=(
                "Diagnose why parse_ints('x') returns [] instead of raising. "
                "Do not change code."
            ),
            hidden_tests=[],
            expected_edit_scope="none",
            ground_truth_root_cause=(
                "broad except Exception absorbs ValueError and returns empty list"
            ),
            known_traps=[],
            intended_clauses=[
                {"kind": "investigation_target", "text": "why parse_ints returns []", "evidence_source": "code"},
                {"kind": "constraint", "text": "Do not change code"},
            ],
            scripted={
                "root_cause_statement": (
                    "broad except Exception absorbs ValueError and returns empty list"
                ),
                "claim_complete": True,
            },
        )
    )

    # --- Review (5) ---
    for i, (iid, prompt, traps) in enumerate(
        [
            (
                "p2-006-review-broad-except",
                "Review revpkg/parse.py for error-handling quality. Do not edit files.",
                ["review-only no edits"],
            ),
            (
                "p2-007-review-calc-ops",
                "Review calcpkg/ops.py average for correctness. Observation: UI shows 4.0. Do not patch.",
                ["P0.2 UI incidental"],
            ),
            (
                "p2-008-review-stock-api",
                "Assess Stock.ship edge cases. Auth middleware is unrelated. No code changes.",
                ["P0.3 auth unrelated"],
            ),
            (
                "p2-009-review-config-api",
                "Review whether Config.get_value naming is clear for new callers. Read-only.",
                [],
            ),
            (
                "p2-010-review-sort-inline",
                "Review bubble.sort_list structure for maintainability. Do not refactor yet.",
                [],
            ),
        ],
        start=6,
    ):
        fixture = {
            6: "reviewlib",
            7: "calc",
            8: "inventory",
            9: "configpkg",
            10: "sorter",
        }[i]
        items.append(
            issue(
                id=iid,
                task_type="review",
                fixture_repo=fixture,
                task_prompt=prompt,
                hidden_tests=[],
                expected_edit_scope="none",
                ground_truth_root_cause=None,
                known_traps=traps,
                intended_clauses=[
                    {"kind": "requested_action", "text": "review code"},
                    {"kind": "constraint", "text": "do not edit"},
                ],
                scripted={
                    "review_findings": ["Found maintainability / correctness concern"],
                    "shell_commands": ["git status"],
                    "claim_complete": True,
                },
            )
        )

    # --- Bugfix (6) ---
    items.append(
        issue(
            id="p2-011-bugfix-average",
            task_type="bugfix",
            fixture_repo="calc",
            task_prompt=(
                "average([2,4]) returns 4.0. I also noticed the API docs mention auth. "
                "Fix the average bug only — do not change divide, and do not touch auth."
            ),
            hidden_tests=["hidden_tests"],
            expected_edit_scope="narrow",
            allowed_edit_globs=["calcpkg/ops.py"],
            ground_truth_root_cause=(
                "average divides by len(nums)-1 off-by-one when length > 1"
            ),
            known_traps=[
                "P1.1 clause classification: observation + constraint + requested_action under noisy phrasing"
            ],
            intended_clauses=[
                {"kind": "observation", "text": "average([2,4]) returns 4.0"},
                {"kind": "observation", "text": "API docs mention auth"},
                {"kind": "requested_action", "text": "Fix the average bug"},
                {"kind": "constraint", "text": "do not change divide"},
                {"kind": "constraint", "text": "do not touch auth"},
            ],
            scripted={
                "file_edits": {"calcpkg/ops.py": FIXED_AVERAGE},
                "root_cause_statement": (
                    "average divides by len(nums)-1 off-by-one when length > 1"
                ),
                "shell_commands": ["python -m unittest discover -s tests -v"],
                "claim_complete": True,
            },
        )
    )
    items.append(
        issue(
            id="p2-012-bugfix-stock",
            task_type="bugfix",
            fixture_repo="inventory",
            task_prompt=(
                "Stock.ship(qty+1) incorrectly succeeds. Fix ship only. "
                "Do not rename Stock."
            ),
            hidden_tests=["hidden_tests"],
            expected_edit_scope="narrow",
            allowed_edit_globs=["invpkg/stock.py"],
            ground_truth_root_cause=(
                "ship uses buggy threshold n > qty+1 allowing one extra unit"
            ),
            known_traps=["P1.1 do not rename constraint"],
            intended_clauses=[
                {"kind": "observation", "text": "ship(qty+1) incorrectly succeeds"},
                {"kind": "requested_action", "text": "Fix ship only"},
                {"kind": "constraint", "text": "Do not rename Stock"},
            ],
            scripted={
                "file_edits": {"invpkg/stock.py": FIXED_STOCK},
                "root_cause_statement": (
                    "ship uses buggy threshold n > qty+1 allowing one extra unit"
                ),
                "shell_commands": ["rg ship invpkg", "python -m unittest discover -s tests -v"],
                "claim_complete": True,
            },
        )
    )
    items.append(
        issue(
            id="p2-013-bugfix-average-with-node",
            task_type="bugfix",
            fixture_repo="calc",
            task_prompt=(
                "Fix average off-by-one. After confirming the cause, only then make changes. "
                "Run tests to verify."
            ),
            hidden_tests=["hidden_tests"],
            expected_edit_scope="narrow",
            allowed_edit_globs=["calcpkg/ops.py"],
            ground_truth_root_cause=(
                "average divides by len(nums)-1 off-by-one when length > 1"
            ),
            known_traps=["P1.2 uncertainty node resolution within live run", "P1.1 process_rule"],
            intended_clauses=[
                {"kind": "requested_action", "text": "Fix average off-by-one"},
                {"kind": "process_rule", "text": "After confirming the cause, only then make changes", "evidence_source": "session"},
                {"kind": "acceptance_criterion", "text": "Run tests to verify"},
            ],
            scripted={
                "file_edits": {"calcpkg/ops.py": FIXED_AVERAGE},
                "root_cause_statement": (
                    "average divides by len(nums)-1 off-by-one when length > 1"
                ),
                "shell_commands": ["python -m unittest discover -s tests -v"],
                "create_uncertainty_node": True,
                "resolve_uncertainty_node": True,
                "claim_complete": True,
            },
        )
    )
    # Extra bugfixes reusing fixtures with slight prompt variants
    for n, (iid, fixture, prompt, edits, globs, cause, filemap) in enumerate(
        [
            (
                "p2-014-bugfix-average-repeat",
                "calc",
                "Bug: average of [1,2,3] is wrong. Fix it. Do not add UI.",
                ["calcpkg/ops.py"],
                ["calcpkg/ops.py"],
                "average divides by len(nums)-1 off-by-one when length > 1",
                {"calcpkg/ops.py": FIXED_AVERAGE},
            ),
            (
                "p2-015-bugfix-stock-repeat",
                "inventory",
                "Users can ship one more than available. Fix Stock.ship. Leave receive alone.",
                ["invpkg/stock.py"],
                ["invpkg/stock.py"],
                "ship uses buggy threshold n > qty+1 allowing one extra unit",
                {"invpkg/stock.py": FIXED_STOCK},
            ),
            (
                "p2-016-bugfix-average-constraint-heavy",
                "calc",
                "Observation: average([2,4])==4. Constraint: do not change add. "
                "Requested: fix average. Also the ticket title says 'API outage' but ignore that.",
                ["calcpkg/ops.py"],
                ["calcpkg/ops.py"],
                "average divides by len(nums)-1 off-by-one when length > 1",
                {"calcpkg/ops.py": FIXED_AVERAGE},
            ),
        ],
        start=14,
    ):
        items.append(
            issue(
                id=iid,
                task_type="bugfix",
                fixture_repo=fixture,
                task_prompt=prompt,
                hidden_tests=["hidden_tests"],
                expected_edit_scope="narrow",
                allowed_edit_globs=globs,
                ground_truth_root_cause=cause,
                known_traps=["noisy ticket phrasing"] if "API" in prompt else [],
                intended_clauses=[
                    {"kind": "requested_action", "text": "fix"},
                    {"kind": "constraint", "text": "constraint"},
                ],
                scripted={
                    "file_edits": filemap,
                    "root_cause_statement": cause,
                    "shell_commands": ["python -m unittest discover -s tests -v"],
                    "claim_complete": True,
                },
            )
        )

    # --- Feature (5) ---
    items.append(
        issue(
            id="p2-017-feature-shout",
            task_type="feature",
            fixture_repo="greeter",
            task_prompt=(
                "Add shout(name) to greetpkg.hello that returns the uppercased name "
                "with a trailing '!'. Do not change greet()."
            ),
            hidden_tests=["hidden_tests"],
            expected_edit_scope="narrow",
            allowed_edit_globs=["greetpkg/hello.py"],
            ground_truth_root_cause=None,
            known_traps=[],
            intended_clauses=[
                {"kind": "requested_action", "text": "Add shout(name)"},
                {"kind": "acceptance_criterion", "text": "uppercased with trailing !"},
                {"kind": "constraint", "text": "Do not change greet()"},
            ],
            scripted={
                "file_edits": {"greetpkg/hello.py": FEATURE_HELLO},
                "shell_commands": ["python -m unittest discover -s tests -v"],
                "claim_complete": True,
            },
        )
    )
    for idx, iid in enumerate(
        [
            "p2-018-feature-shout-b",
            "p2-019-feature-shout-c",
            "p2-020-feature-shout-d",
            "p2-021-feature-shout-e",
        ],
        start=18,
    ):
        items.append(
            issue(
                id=iid,
                task_type="feature",
                fixture_repo="greeter",
                task_prompt=(
                    f"Implement shout(name) returning NAME! in greetpkg.hello "
                    f"(variant {idx}). Keep greet unchanged. No auth work."
                ),
                hidden_tests=["hidden_tests"],
                expected_edit_scope="narrow",
                allowed_edit_globs=["greetpkg/hello.py"],
                known_traps=["auth incidental"] if idx == 18 else [],
                intended_clauses=[
                    {"kind": "requested_action", "text": "Implement shout"},
                    {"kind": "constraint", "text": "Keep greet unchanged"},
                ],
                scripted={
                    "file_edits": {"greetpkg/hello.py": FEATURE_HELLO},
                    "claim_complete": True,
                },
            )
        )

    # --- Migration (5) — command-heavy for P0.5 ---
    mig_cmds = [
        "git status",
        "rg get_value cfg",
        "rg get_value",
        "python -m unittest discover -s tests -v",
    ]
    items.append(
        issue(
            id="p2-022-migration-get-value",
            task_type="migration",
            fixture_repo="configpkg",
            task_prompt=(
                "Migrate Config.get_value → Config.get across cfg/, update all call sites "
                "in this package, and re-run the visible test suite. Grep the repo for "
                "remaining get_value usages before finishing."
            ),
            hidden_tests=["hidden_tests"],
            expected_edit_scope="broad",
            allowed_edit_globs=["cfg/*.py"],
            known_traps=[
                "P0.5 command risk classes under command-heavy migration (grep + repeated tests)"
            ],
            intended_clauses=[
                {"kind": "requested_action", "text": "Migrate get_value to get"},
                {"kind": "process_rule", "text": "Grep for remaining get_value", "evidence_source": "session"},
                {"kind": "acceptance_criterion", "text": "re-run the visible test suite"},
            ],
            scripted={
                "file_edits": {
                    "cfg/settings.py": MIGRATED_SETTINGS,
                    "cfg/app.py": MIGRATED_APP,
                },
                "shell_commands": mig_cmds,
                "claim_complete": True,
            },
        )
    )
    for iid in [
        "p2-023-migration-get-value-b",
        "p2-024-migration-get-value-c",
        "p2-025-migration-get-value-d",
        "p2-026-migration-get-value-e",
    ]:
        items.append(
            issue(
                id=iid,
                task_type="migration",
                fixture_repo="configpkg",
                task_prompt=(
                    "Rename get_value to get on Config and update cfg/app.py call sites. "
                    "Use rg/git status freely; run unittest discover after edits."
                ),
                hidden_tests=["hidden_tests"],
                expected_edit_scope="broad",
                allowed_edit_globs=["cfg/*.py"],
                known_traps=["P0.5 read-only commands should not interrupt"],
                intended_clauses=[
                    {"kind": "requested_action", "text": "Rename get_value to get"},
                ],
                scripted={
                    "file_edits": {
                        "cfg/settings.py": MIGRATED_SETTINGS,
                        "cfg/app.py": MIGRATED_APP,
                    },
                    "shell_commands": mig_cmds,
                    "claim_complete": True,
                },
            )
        )

    # --- Refactor (4) ---
    items.append(
        issue(
            id="p2-027-refactor-extract-swap",
            task_type="refactor",
            fixture_repo="sorter",
            task_prompt=(
                "Refactor sortpkg/bubble.py to extract a swap(xs, i, j) helper. "
                "Behavior must stay identical — verify with the existing tests."
            ),
            hidden_tests=["hidden_tests"],
            expected_edit_scope="narrow",
            allowed_edit_globs=["sortpkg/bubble.py"],
            known_traps=[],
            intended_clauses=[
                {"kind": "requested_action", "text": "extract swap helper"},
                {"kind": "acceptance_criterion", "text": "Behavior must stay identical"},
                {"kind": "process_rule", "text": "verify with existing tests", "evidence_source": "session"},
            ],
            scripted={
                "file_edits": {"sortpkg/bubble.py": REFAC_BUBBLE},
                "shell_commands": [
                    "git status",
                    "python -m unittest discover -s tests -v",
                ],
                "claim_complete": True,
            },
        )
    )
    for iid in [
        "p2-028-refactor-extract-swap-b",
        "p2-029-refactor-extract-swap-c",
        "p2-030-refactor-extract-swap-d",
    ]:
        items.append(
            issue(
                id=iid,
                task_type="refactor",
                fixture_repo="sorter",
                task_prompt=(
                    "Extract swap from the inline temp exchange in bubble.sort_list. "
                    "Do not change sort order semantics. Re-run tests."
                ),
                hidden_tests=["hidden_tests"],
                expected_edit_scope="narrow",
                allowed_edit_globs=["sortpkg/bubble.py"],
                intended_clauses=[
                    {"kind": "requested_action", "text": "Extract swap"},
                    {"kind": "constraint", "text": "Do not change sort order semantics"},
                ],
                scripted={
                    "file_edits": {"sortpkg/bubble.py": REFAC_BUBBLE},
                    "shell_commands": ["python -m unittest discover -s tests -v"],
                    "claim_complete": True,
                },
            )
        )

    assert len(items) == 30, len(items)
    for it in items:
        path = ISSUES / f"{it['id']}.json"
        write(path, json.dumps(it, indent=2) + "\n")
    print(f"Wrote {len(items)} issues to {ISSUES}")


def main() -> None:
    FIX.mkdir(parents=True, exist_ok=True)
    gen_fixture_calc()
    gen_fixture_inventory()
    gen_fixture_greeter()
    gen_fixture_configpkg()
    gen_fixture_sorter()
    gen_fixture_reviewlib()
    gen_issues()
    print("Fixtures + issues generated.")


if __name__ == "__main__":
    main()
