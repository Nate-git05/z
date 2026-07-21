import unittest
from sortpkg import sort_list


class VisibleTests(unittest.TestCase):
    def test_sort(self):
        self.assertEqual(sort_list([3, 1, 2]), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
