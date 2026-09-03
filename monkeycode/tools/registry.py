from __future__ import annotations

from monkeycode.messages import ToolDefinition
from monkeycode.tools.base import Tool, ToolPolicy


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def register_skill_tool(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def policy(self, name: str) -> ToolPolicy:
        tool = self._tools.get(name)
        if tool is None:
            return ToolPolicy(tool_name=name)
        return getattr(tool, "policy", ToolPolicy(tool_name=tool.name))

    def definitions(
        self,
        *,
        allowed_names: set[str] | None = None,
        include_system: bool = False,
    ) -> list[ToolDefinition]:
        from monkeycode.prompting import enhance_tool_definition

        return [
            enhance_tool_definition(
                ToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    parameters_schema=tool.parameters_schema,
                )
            )
            for tool in self._tools.values()
            if allowed_names is None
            or tool.name in allowed_names
            or (include_system and _is_system(tool))
        ]

    def definitions_filtered(self, allowed: list[str] | set[str]) -> list[ToolDefinition]:
        allowed_names = set(allowed)
        from monkeycode.prompting import enhance_tool_definition

        return [
            enhance_tool_definition(
                ToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    parameters_schema=tool.parameters_schema,
                )
            )
            for tool in self._tools.values()
            if tool.name in allowed_names or _is_system(tool)
        ]

    def system_names(self) -> set[str]:
        return {name for name, tool in self._tools.items() if _is_system(tool)}

    def names(self) -> list[str]:
        return list(self._tools)

    def count(self) -> int:
        return len(self._tools)


def _is_system(tool: Tool) -> bool:
    return bool(getattr(tool, "is_system", False))
