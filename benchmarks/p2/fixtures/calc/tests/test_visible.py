import unittest
from calcpkg import add, divide


class VisibleTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_divide(self):
        self.assertEqual(divide(10, 2), 5.0)


if __name__ == "__main__":
    unittest.main()
