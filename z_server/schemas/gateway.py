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
    tier: Optional[str] = None

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
