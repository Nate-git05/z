"""App-server JSON-RPC encoding must tolerate datetime values."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone


class AppServerJsonTest(unittest.TestCase):
    def test_dumps_datetime(self):
        from aider.z.app_server.server import _dumps

        ts = datetime(2026, 7, 22, 1, 2, 3, tzinfo=timezone.utc)
        raw = _dumps({"ok": True, "updated_at": ts})
        data = json.loads(raw)
        self.assertTrue(data["ok"])
        self.assertEqual(data["updated_at"], "2026-07-22T01:02:03+00:00")

    def test_skill_summary_coerces_datetime(self):
        from aider.z.app_server.handlers import AppServerSession
        from types import SimpleNamespace

        s = SimpleNamespace(
            id="abc",
            title="t",
            kind="playbook",
            description="",
            triggers=[],
            capability="",
            quality_state="verified",
            needs_review=False,
            source="generate",
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            symptom_description="",
            root_cause_category="",
        )
        summary = AppServerSession._skill_summary(s)
        self.assertEqual(summary["updated_at"], "2026-01-01T00:00:00+00:00")
        json.dumps(summary)  # must not raise


if __name__ == "__main__":
    unittest.main()
