"""Pydantic schemas for the routing gateway (OpenAI-compatible surface)."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: Any = ""


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # Z extensions (ignored by OpenAI SDKs that pass unknown fields away)
    thread_id: Optional[str] = Field(default=None, alias="thread_id")
    task_mode: Optional[str] = None
    intent: Optional[str] = None
    tier: Optional[str] = None
    escalate: bool = False
    escalation_depth: int = 0
    preferred_model: Optional[str] = None

    model_config = {"populate_by_name": True, "extra": "allow"}


class UsageRow(BaseModel):
    model_id: str
    requests: int
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class UsageSummary(BaseModel):
    range: str
    by_model: List[UsageRow]
    total_requests: int = 0
    total_cost_usd: float = 0.0


class RoutingOutcomeRequest(BaseModel):
    """Client → gateway calibration hook after local verify/commit gate."""

    model_id: str
    tier: str = "moderate"
    gate_passed: bool
    escalated: bool = False
    cost_usd: float = 0.0
    checker_triggered: Optional[str] = None
    thread_id: Optional[str] = None


class RoutingOutcomeResponse(BaseModel):
    ok: bool = True
    model_id: str
    task_category: str
    gate_passed: bool
    escalated: bool = False
    routing_policy_version: str
