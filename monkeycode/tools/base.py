from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from monkeycode.messages import ToolDefinition


@dataclass(frozen=True)
class ToolContext:
    workspace_root: Path
    default_timeout_seconds: float = 10.0
    max_output_chars: int = 12000


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    success: bool
    output: dict[str, Any] | str | None = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "output": self.output,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ToolPolicy:
    tool_name: str
    category: str = "side_effect"
    allowed_in_plan_mode: bool = False
    can_run_parallel: bool = False
    has_side_effects: bool = True


class Tool(Protocol):
    @property
    def name(self) -> str:
        ...

    @property
    def description(self) -> str:
        ...

    @property
    def parameters_schema(self) -> dict[str, Any]:
        ...

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(tool_name=self.name)

    @property
    def is_system(self) -> bool:
        return False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        ...

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters_schema=self.parameters_schema,
        )
