import unittest
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
