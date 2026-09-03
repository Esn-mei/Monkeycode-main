from pathlib import Path

from monkeycode.events import AgentMode, CancellationToken
from monkeycode.messages import ToolCall
from monkeycode.tool_scheduler import ToolScheduler
from monkeycode.tools import create_default_executor


def call(name: str, arguments: dict) -> ToolCall:
    import json

    return ToolCall(
        id=f"call_{name}",
        name=name,
        arguments_json=json.dumps(arguments),
        arguments=arguments,
    )


def test_plan_mode_allows_read_tools(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    scheduler = ToolScheduler(create_default_executor(tmp_path))

    list(
        scheduler.run_tool_calls(
            [
                call("read_file", {"path": "README.md"}),
                call("find_files", {"pattern": "*.md"}),
                call("search_code", {"query": "hello"}),
            ],
            mode=AgentMode.PLAN,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert [result.success for _, result in scheduler.results] == [True, True, True]


def test_plan_mode_rejects_write_edit_and_command(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("old", encoding="utf-8")
    scheduler = ToolScheduler(create_default_executor(tmp_path))

    list(
        scheduler.run_tool_calls(
            [
                call("write_file", {"path": "created.txt", "content": "new"}),
                call("edit_file", {"path": "note.txt", "old_text": "old", "new_text": "new"}),
                call("run_command", {"command": "echo should-not-run"}),
            ],
            mode=AgentMode.PLAN,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert [result.error_type for _, result in scheduler.results] == [
        "tool_not_allowed_in_plan_mode",
        "tool_not_allowed_in_plan_mode",
        "tool_not_allowed_in_plan_mode",
    ]
    assert not (tmp_path / "created.txt").exists()
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "old"

