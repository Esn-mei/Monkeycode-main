from __future__ import annotations

from monkeycode.messages import ToolCall
from monkeycode.permissions import PermissionMode
from monkeycode.skills.active import ActiveSkills
from monkeycode.skills.catalog import Catalog
from monkeycode.skills.parser import parse_skill_dir
from monkeycode.skills.types import SkillSource
from monkeycode.tools.base import ToolContext
from monkeycode.tools.executor import ToolExecutor
from monkeycode.tools.load_skill import LoadSkillTool
from monkeycode.tools.registry import ToolRegistry


def test_load_skill_activates_body_and_registers_tool(tmp_path) -> None:
    skill_dir = tmp_path / "demo"
    (skill_dir / "references").mkdir(parents=True)
    script = skill_dir / "references" / "echo_tool.py"
    script.write_text("import sys\nprint('ok')\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo\n---\nBody\n",
        encoding="utf-8",
    )
    (skill_dir / "tool.json").write_text(
        '{"tools":[{"name":"echo-tool","description":"Echo","input_schema":{"type":"object","properties":{}},"command":["python","references/echo_tool.py"]}]}',
        encoding="utf-8",
    )
    catalog = Catalog()
    catalog.register(parse_skill_dir(skill_dir, SkillSource.PROJECT))
    active = ActiveSkills()
    registry = ToolRegistry()

    result = LoadSkillTool(catalog, active, registry).execute(
        {"name": "demo"},
        ToolContext(workspace_root=tmp_path),
    )

    assert result.success is True
    assert active.names() == ["demo"]
    assert registry.get("echo-tool") is not None


def test_load_skill_unknown_name_returns_error(tmp_path) -> None:
    result = LoadSkillTool(Catalog(), ActiveSkills(), ToolRegistry()).execute(
        {"name": "missing"},
        ToolContext(workspace_root=tmp_path),
    )

    assert result.success is False
    assert result.error_message == "unknown skill: missing"


def test_load_skill_allowed_in_plan_mode(tmp_path) -> None:
    registry = ToolRegistry()
    catalog = Catalog()
    active = ActiveSkills()
    registry.register(LoadSkillTool(catalog, active, registry))
    executor = ToolExecutor(
        registry, workspace_root=tmp_path, permission_mode=PermissionMode.DEFAULT
    )
    call = ToolCall(id="1", name="load_skill", arguments_json='{"name":"missing"}')

    result = executor.execute(call)

    assert result.error_type == "unknown_skill"
