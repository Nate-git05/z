"""Shared test isolation fixtures for tests/basic.

``aider.z.gateway_client`` mutates real ``os.environ`` state (gateway routing
flags, OPENAI_API_BASE/KEY) as a deliberate side effect for the life of a real
``z`` process. Any test that exercises the real (unmocked) router path in
``ensure_agent_session``/``_start_agent`` leaves that state behind for every
later test in the same pytest process — e.g. a later test expecting a plain
model name instead sees a gateway-prefixed one because an earlier,
unrelated test set ``Z_GATEWAY_ACTIVE=1`` and never cleared it.
"""

from __future__ import annotations

import os

import pytest

_GATEWAY_ENV_KEYS = (
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "Z_GATEWAY_ACTIVE",
    "Z_GATEWAY_MODEL",
    "Z_GATEWAY_TASK_MODE",
    "Z_GATEWAY_INTENT",
    "Z_GATEWAY_ESCALATE",
    "Z_GATEWAY_ESCALATION_DEPTH",
    "Z_GATEWAY_THREAD_ID",
)


@pytest.fixture(autouse=True)
def _isolate_gateway_env():
    saved = {k: os.environ.get(k) for k in _GATEWAY_ENV_KEYS}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
