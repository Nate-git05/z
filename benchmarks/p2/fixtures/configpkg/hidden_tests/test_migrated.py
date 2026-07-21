import unittest
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
