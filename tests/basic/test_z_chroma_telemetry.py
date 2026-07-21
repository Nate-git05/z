"""Chroma product-telemetry silence (fault-plan chroma-telemetry slice)."""

from __future__ import annotations

import io
import logging
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock


def _has_chromadb() -> bool:
    try:
        import chromadb  # noqa: F401

        return True
    except ImportError:
        return False


class ConfigureChromaTelemetryTest(unittest.TestCase):
    def setUp(self):
        # Isolate env for assertions; restore in tearDown
        self._prev_anon = os.environ.get("ANONYMIZED_TELEMETRY")
        self._prev_chroma = os.environ.get("CHROMA_ANONYMIZED_TELEMETRY")
        self._prev_verbose = os.environ.get("Z_VERBOSE")
        for key in (
            "ANONYMIZED_TELEMETRY",
            "CHROMA_ANONYMIZED_TELEMETRY",
            "Z_VERBOSE",
        ):
            os.environ.pop(key, None)
        # Allow re-entry of configure for patch path
        import aider.z.skills.vector as vector_mod

        self._vector = vector_mod
        self._prev_configured = vector_mod._TELEMETRY_CONFIGURED
        vector_mod._TELEMETRY_CONFIGURED = False

    def tearDown(self):
        def _restore(key, prev):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev

        _restore("ANONYMIZED_TELEMETRY", self._prev_anon)
        _restore("CHROMA_ANONYMIZED_TELEMETRY", self._prev_chroma)
        _restore("Z_VERBOSE", self._prev_verbose)
        self._vector._TELEMETRY_CONFIGURED = self._prev_configured

    def test_setdefault_env_flags(self):
        from aider.z.skills.vector import configure_chroma_telemetry

        configure_chroma_telemetry()
        self.assertEqual(os.environ.get("ANONYMIZED_TELEMETRY"), "False")
        self.assertEqual(os.environ.get("CHROMA_ANONYMIZED_TELEMETRY"), "False")

    def test_does_not_override_existing_env(self):
        os.environ["ANONYMIZED_TELEMETRY"] = "True"
        from aider.z.skills.vector import configure_chroma_telemetry

        configure_chroma_telemetry()
        self.assertEqual(os.environ.get("ANONYMIZED_TELEMETRY"), "True")

    def test_silences_posthog_logger_by_default(self):
        from aider.z.skills.vector import configure_chroma_telemetry

        configure_chroma_telemetry()
        log = logging.getLogger("chromadb.telemetry.product.posthog")
        self.assertTrue(log.disabled)

    def test_verbose_leaves_logger_enabled(self):
        os.environ["Z_VERBOSE"] = "1"
        log = logging.getLogger("chromadb.telemetry.product.posthog")
        log.disabled = False
        log.setLevel(logging.WARNING)
        from aider.z.skills.vector import configure_chroma_telemetry

        configure_chroma_telemetry()
        self.assertFalse(log.disabled)


@unittest.skipUnless(_has_chromadb(), "chromadb not installed")
class ChromaTelemetryInitTest(unittest.TestCase):
    def setUp(self):
        self._prev_anon = os.environ.get("ANONYMIZED_TELEMETRY")
        self._prev_verbose = os.environ.get("Z_VERBOSE")
        os.environ.pop("Z_VERBOSE", None)
        import aider.z.skills.vector as vector_mod

        self._vector = vector_mod
        self._prev_configured = vector_mod._TELEMETRY_CONFIGURED
        vector_mod._TELEMETRY_CONFIGURED = False
        # Reset Posthog noop flag so configure can re-patch
        try:
            from chromadb.telemetry.product import posthog as chroma_posthog

            if hasattr(chroma_posthog.Posthog, "_z_capture_noop"):
                delattr(chroma_posthog.Posthog, "_z_capture_noop")
            self._orig_capture = chroma_posthog.Posthog.capture
        except Exception:
            self._orig_capture = None

    def tearDown(self):
        if self._prev_anon is None:
            os.environ.pop("ANONYMIZED_TELEMETRY", None)
        else:
            os.environ["ANONYMIZED_TELEMETRY"] = self._prev_anon
        if self._prev_verbose is None:
            os.environ.pop("Z_VERBOSE", None)
        else:
            os.environ["Z_VERBOSE"] = self._prev_verbose
        self._vector._TELEMETRY_CONFIGURED = self._prev_configured
        if self._orig_capture is not None:
            try:
                from chromadb.telemetry.product import posthog as chroma_posthog

                chroma_posthog.Posthog.capture = self._orig_capture
                if hasattr(chroma_posthog.Posthog, "_z_capture_noop"):
                    delattr(chroma_posthog.Posthog, "_z_capture_noop")
            except Exception:
                pass

    def test_skill_vector_init_no_capture_typeerror_on_stderr(self):
        from aider.z.skills.vector import SkillVectorIndex, configure_chroma_telemetry

        configure_chroma_telemetry()
        root = Path(tempfile.mkdtemp(prefix="z_chroma_tel_"))
        buf = io.StringIO()
        with redirect_stderr(buf):
            index = SkillVectorIndex(persist_dir=root)
            # Force client + collection creation (fires ClientStartEvent)
            self.assertEqual(index.count(), 0)
        err = buf.getvalue()
        self.assertNotIn("ClientStartEvent", err)
        self.assertNotIn("capture() takes 1 positional argument", err)
        self.assertNotIn("Failed to send telemetry event", err)

    def test_unpatched_chroma_still_emits_without_configure(self):
        """Sanity: upstream chroma+posthog mismatch still produces the spam.

        Skipped if capture already no-op'd by a prior test in this process that
        we could not restore — then this assertion would be meaningless.
        """
        import chromadb
        from chromadb.config import Settings
        from chromadb.telemetry.product import posthog as chroma_posthog

        # Restore real capture for this probe
        if self._orig_capture is not None:
            chroma_posthog.Posthog.capture = self._orig_capture
            if hasattr(chroma_posthog.Posthog, "_z_capture_noop"):
                delattr(chroma_posthog.Posthog, "_z_capture_noop")

        # Re-enable logger so we can observe the error line
        log = logging.getLogger("chromadb.telemetry.product.posthog")
        log.disabled = False
        log.setLevel(logging.ERROR)
        handler = logging.StreamHandler()
        log.addHandler(handler)

        td = tempfile.mkdtemp(prefix="z_chroma_raw_")
        buf = io.StringIO()
        handler.stream = buf  # type: ignore[attr-defined]
        try:
            # Force telemetry path: Settings True still hits broken capture
            with mock.patch.dict(os.environ, {"ANONYMIZED_TELEMETRY": "True"}, clear=False):
                chromadb.PersistentClient(
                    path=td,
                    settings=Settings(anonymized_telemetry=True),
                )
        finally:
            log.removeHandler(handler)

        # If posthog API is fixed upstream this may be empty — then skip.
        err = buf.getvalue()
        if "capture() takes 1 positional argument" not in err and "ClientStartEvent" not in err:
            self.skipTest("upstream chroma/posthog no longer emits capture TypeError")
        self.assertTrue(
            "ClientStartEvent" in err or "capture() takes 1 positional argument" in err
        )


if __name__ == "__main__":
    unittest.main()
