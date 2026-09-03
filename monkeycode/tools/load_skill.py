from __future__ import annotations

from typing import Any

from monkeycode.skills.active import ActiveSkills
from monkeycode.skills.catalog import Catalog
from monkeycode.skills.parser import read_skill_body
from monkeycode.tools.base import ToolContext, ToolPolicy, ToolResult
from monkeycode.tools.registry import ToolRegistry
from monkeycode.tools.skill_tool import new_skill_tool


class LoadSkillTool:
    name = "load_skill"
    description = (
        "Activate a MonkeyCode Skill SOP by name and register its specialized tools."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name to activate",
            }
        },
        "required": ["name"],
    }

    def __init__(
        self, catalog: Catalog, active: ActiveSkills, registry: ToolRegistry
    ) -> None:
        self.catalog = catalog
        self.active = active
        self.registry = registry

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(
            tool_name=self.name,
            category="read",
            allowed_in_plan_mode=True,
            has_side_effects=False,
        )

    @property
    def is_system(self) -> bool:
        return True

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        name = str(arguments.get("name", ""))
        skill = self.catalog.get(name)
        if skill is None:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error_type="unknown_skill",
                error_message=f"unknown skill: {name}",
            )

        fresh_body = read_skill_body(skill)
        self.active.activate(skill.meta.name, fresh_body)
        for spec in skill.tool_specs:
            self.registry.register_skill_tool(
                new_skill_tool(
                    spec.name,
                    spec.description,
                    spec.input_schema,
                    spec.command,
                    spec.base_dir,
                )
            )
        return ToolResult(
            tool_name=self.name,
            success=True,
            output=(
                f"Skill {skill.meta.name} activated. SOP pinned to env context. "
                f"{len(skill.tool_specs)} specialized tools registered."
            ),
        )
