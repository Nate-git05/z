"""DATABASE_URL normalization for Supabase / Cloud Run."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("Z_SECRET_KEY", "test")
os.environ.setdefault("Z_SERVER_DEV", "1")

from z_server.config import _normalize_database_url, get_settings  # noqa: E402


class DatabaseUrlNormalizeTest(unittest.TestCase):
    def test_postgres_scheme_upgraded_and_ssl_added(self):
        url = _normalize_database_url(
            "postgresql://postgres:secret@db.abc.supabase.co:5432/postgres"
        )
        self.assertTrue(url.startswith("postgresql+psycopg://"))
        self.assertIn("sslmode=require", url)
        self.assertIn("db.abc.supabase.co", url)

    def test_postgres_short_scheme(self):
        url = _normalize_database_url(
            "postgres://postgres:secret@db.abc.supabase.co:5432/postgres"
        )
        self.assertTrue(url.startswith("postgresql+psycopg://"))
        self.assertIn("sslmode=require", url)

    def test_localhost_skips_ssl(self):
        url = _normalize_database_url(
            "postgresql://z:z@localhost:5432/z"
        )
        self.assertEqual(url, "postgresql+psycopg://z:z@localhost:5432/z")
        self.assertNotIn("sslmode", url)

    def test_existing_sslmode_preserved(self):
        url = _normalize_database_url(
            "postgresql+psycopg://postgres:x@db.abc.supabase.co:5432/postgres?sslmode=disable"
        )
        self.assertIn("sslmode=disable", url)
        self.assertNotIn("sslmode=require", url)

    def test_settings_reads_env(self):
        get_settings.cache_clear()
        os.environ["DATABASE_URL"] = (
            "postgresql://postgres:x@db.abc.supabase.co:5432/postgres"
        )
        try:
            settings = get_settings()
            self.assertTrue(settings.database_url.startswith("postgresql+psycopg://"))
            self.assertIn("sslmode=require", settings.database_url)
        finally:
            os.environ.pop("DATABASE_URL", None)
            get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
