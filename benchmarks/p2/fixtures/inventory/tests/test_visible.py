import unittest
from invpkg import Stock


class VisibleTests(unittest.TestCase):
    def test_receive(self):
        s = Stock("a", 0)
        s.receive(5)
        self.assertEqual(s.qty, 5)


if __name__ == "__main__":
    unittest.main()
