import unittest
from revpkg import parse_ints


class VisibleTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(parse_ints("1,2,3"), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
