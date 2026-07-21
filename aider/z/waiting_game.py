"""Waiting display factory for Z model waits.

Uses the compact single-line mascot spinner (same look as before the
interactive runner / spiral experiments). Kept as a thin module so
coder/repo import sites stay stable.
"""

from __future__ import annotations

from .mascot import MascotSpinner


def waiting_display(text: str, *, interactive: bool | None = None):
    """
    Factory used by the coder/repo wait hooks.

    Always returns the compact ``MascotSpinner``. ``interactive`` is accepted
    for call-site compatibility and ignored.
    """
    _ = interactive
    return MascotSpinner(text)


# Back-compat aliases (older imports / tests)
SpiralWaiting = MascotSpinner
AgentRunnerGame = MascotSpinner
MascotRunnerGame = MascotSpinner
