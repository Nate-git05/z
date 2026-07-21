"""Typed exception classes for Z agent failure policy (P1.3).

Default for unclassified failures: treat as integrity (fail closed).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger("aider.z.errors")


class OptionalSubsystemError(Exception):
    """Failure must not affect agent behavior beyond being logged.

    Examples: telemetry, remote sync, non-critical detectors.
    """


class RecoverableAgentError(Exception):
    """Failure may degrade behavior visibly but must not corrupt correctness.

    Examples: optional presentation steps, non-required planning display.
    """


class IntegrityGateError(Exception):
    """Silent failure could report success without verification — fail closed.

    Examples: verification checks, completion decisions, evidence binding.
    """

    def __init__(self, message: str, *, diagnostic_id: Optional[str] = None):
        super().__init__(message)
        self.diagnostic_id = diagnostic_id or make_diagnostic_id()


def make_diagnostic_id() -> str:
    return f"zdiag-{uuid.uuid4().hex[:12]}"


def classify_exception(exc: BaseException) -> type:
    """Map an exception instance to a policy class (default: IntegrityGateError)."""
    if isinstance(
        exc, (OptionalSubsystemError, RecoverableAgentError, IntegrityGateError)
    ):
        return type(exc)
    # Explicit known optional I/O
    name = type(exc).__name__
    if name in ("RequestException", "Timeout", "ConnectionError", "HTTPError"):
        return OptionalSubsystemError
    return IntegrityGateError


def handle_classified(
    exc: BaseException,
    *,
    context: str = "",
    io: Any = None,
    planning_required: bool = False,
) -> str:
    """
    Apply policy for a caught exception.

    Returns one of: ``"continue"``, ``"degraded"``, ``"fail_closed"``.
    """
    cls = classify_exception(exc)
    diag = getattr(exc, "diagnostic_id", None) or make_diagnostic_id()
    msg = f"{context}: {exc}" if context else str(exc)

    if cls is OptionalSubsystemError or (
        not isinstance(exc, (RecoverableAgentError, IntegrityGateError))
        and cls is OptionalSubsystemError
    ):
        logger.warning("optional_subsystem_error diag=%s %s", diag, msg)
        return "continue"

    if isinstance(exc, OptionalSubsystemError) or cls is OptionalSubsystemError:
        logger.warning("optional_subsystem_error diag=%s %s", diag, msg)
        return "continue"

    if isinstance(exc, RecoverableAgentError) or (
        cls is RecoverableAgentError and not isinstance(exc, IntegrityGateError)
    ):
        if planning_required:
            # Planning was required — escalate
            logger.error(
                "recoverable_escalated_to_integrity diag=%s %s", diag, msg
            )
            if io is not None and hasattr(io, "tool_error"):
                io.tool_error(
                    f"Integrity gate: planning/verification could not complete "
                    f"[{diag}]: {exc}"
                )
            return "fail_closed"
        logger.warning("recoverable_agent_error diag=%s %s", diag, msg)
        if io is not None and hasattr(io, "tool_warning"):
            io.tool_warning(f"Degraded: {exc} [{diag}]")
        return "degraded"

    # IntegrityGateError or unclassified default
    logger.error("integrity_gate_error diag=%s %s", diag, msg, exc_info=exc)
    if io is not None and hasattr(io, "tool_error"):
        io.tool_error(
            f"Verification could not be completed [{diag}]: {exc}. "
            "Not claiming success."
        )
    return "fail_closed"


# Outermost allowlist: modules/paths where bare ``except Exception`` remains
# intentional after review (top-level agent loop / best-effort UI only).
BARE_EXCEPT_ALLOWLIST = frozenset(
    {
        "aider/coders/base_coder.py:run",  # outermost message loop
        "aider/z/uncertainty/ui.py",  # presentation
        "aider/z/uncertainty/detector_debug.py",
        "aider/z/uncertainty/sync_outbox.py",  # worker must not die
    }
)
