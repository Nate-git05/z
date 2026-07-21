import unittest
from calcpkg import average


class HiddenAverageTests(unittest.TestCase):
    def test_average_two(self):
        self.assertEqual(average([2, 4]), 3.0)

    def test_average_three(self):
        self.assertAlmostEqual(average([1, 2, 3]), 2.0)


if __name__ == "__main__":
    unittest.main()
