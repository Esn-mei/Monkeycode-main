from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from monkeycode.messages import ToolCall
from monkeycode.tools.base import ToolResult


class AgentMode(str, Enum):
    CHAT = "chat"
    PLAN = "plan"
    EXECUTE = "execute"


@dataclass(frozen=True)
class AgentConfig:
    max_iterations: int = 10
    max_consecutive_unknown_tools: int = 2
    default_tool_timeout_seconds: float = 10.0
    max_parallel_tools: int = 4
    soft_tool_budget: int = 6
    max_output_chars: int = 12000


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


@dataclass(frozen=True)
class AgentEvent:
    type: str
    turn_index: int = 0
    iteration: int = 0
    text: str | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    usage: dict[str, Any] | None = None
    progress: str | None = None
    mode: AgentMode | None = None
    stop_reason: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
