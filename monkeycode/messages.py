from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    provider_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str
    arguments: dict[str, Any] | None = None
    provider: str = ""


@dataclass(frozen=True)
class StreamEvent:
    type: Literal["text_delta", "reasoning_delta", "tool_call", "usage", "error", "done"]
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: dict[str, Any] | None = None
    cache_usage: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
