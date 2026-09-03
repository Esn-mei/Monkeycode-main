from __future__ import annotations

from pathlib import Path
from typing import Any

from monkeycode.skills.catalog import Catalog
from monkeycode.skills.install import install_from_source
from monkeycode.tools.base import ToolContext, ToolPolicy, ToolResult


class InstallSkillTool:
    name = "install_skill"
    description = (
        "Install a MonkeyCode Skill into ~/.monkeycode/skills/ from an HTTP(S) zip URL, "
        "file URL, local .zip path, or local skill directory containing SKILL.md."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "HTTP(S) zip URL, file URL, local .zip path, or local skill directory",
            }
        },
        "required": ["source"],
    }

    def __init__(self, catalog: Catalog, work_dir: Path) -> None:
        self.catalog = catalog
        self.work_dir = work_dir

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(tool_name=self.name, category="side_effect")

    @property
    def is_system(self) -> bool:
        return False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        source = str(arguments.get("source", ""))
        try:
            name = install_from_source(source, self.catalog, self.work_dir)
        except Exception as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error_type="install_skill_failed",
                error_message=str(exc),
            )
        return ToolResult(
            tool_name=self.name,
            success=True,
            output=f"Skill {name} installed to ~/.monkeycode/skills/{name}.",
        )
