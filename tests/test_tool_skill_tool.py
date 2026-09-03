from __future__ import annotations

from monkeycode.tools.base import ToolContext
from monkeycode.tools.skill_tool import new_skill_tool


def test_skill_tool_exec_success(tmp_path) -> None:
    script = tmp_path / "ok.py"
    script.write_text("import sys\nprint('ok')\n", encoding="utf-8")
    tool = new_skill_tool(
        "parse-resume",
        "Parse",
        {"type": "object", "properties": {}},
        ["python", "ok.py"],
        tmp_path,
    )

    result = tool.execute({}, ToolContext(workspace_root=tmp_path))

    assert result.success is True
    assert result.output.strip() == "ok"


def test_skill_tool_exec_failure_includes_stderr(tmp_path) -> None:
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys\nprint('bad', file=sys.stderr)\nsys.exit(3)\n", encoding="utf-8"
    )
    tool = new_skill_tool(
        "parse-resume",
        "Parse",
        {"type": "object", "properties": {}},
        ["python", "fail.py"],
        tmp_path,
    )

    result = tool.execute({}, ToolContext(workspace_root=tmp_path))

    assert result.success is False
    assert result.error_type == "command_failed"
    assert "bad" in result.output
