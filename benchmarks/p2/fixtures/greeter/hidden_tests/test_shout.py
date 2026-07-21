import unittest
from greetpkg.hello import shout


class HiddenShoutTests(unittest.TestCase):
    def test_shout(self):
        self.assertEqual(shout("ada"), "ADA!")


if __name__ == "__main__":
    unittest.main()
