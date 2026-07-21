import unittest
from cfg import Config


class VisibleTests(unittest.TestCase):
    def test_get_value(self):
        c = Config({"a": 1})
        self.assertEqual(c.get_value("a"), 1)


if __name__ == "__main__":
    unittest.main()
