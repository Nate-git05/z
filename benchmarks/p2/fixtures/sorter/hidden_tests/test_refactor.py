import unittest
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
