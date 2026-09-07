from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Effort = Literal["fast", "standard", "deep"]


class RouteDecision(BaseModel):
    effort: Effort | None
    source: Literal["explicit", "semantic", "abstain"]
    score: float | None = None
    margin: float | None = None
    reason: str
    router_state: str = "unknown"
    matched_text: str | None = None
    window_count: int = 0


class ToolCall(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ProviderChunk(BaseModel):
    type: Literal["reasoning", "content", "tool_call", "usage", "finish"]
    delta: str | None = None
    tool_call: ToolCall | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None


class StreamEvent(BaseModel):
    request_id: str
    sequence: int
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
