import json
from pathlib import Path

from monkeycode.config import ContextConfig
from monkeycode.context import ToolResultArchiver
from monkeycode.messages import ChatMessage, ToolCall
from monkeycode.session import ChatSession


def _tool_call(ident: str) -> ToolCall:
    return ToolCall(id=ident, name="read_file", arguments_json="{}", arguments={})


def _tool_result(tool_name: str, text: str) -> str:
    return json.dumps(
        {
            "tool_name": tool_name,
            "success": True,
            "output": {"content": text},
            "error_type": None,
            "error_message": None,
            "metadata": {},
        },
        ensure_ascii=False,
    )


def test_single_large_tool_result_is_archived(tmp_path: Path) -> None:
    session = ChatSession()
    session.add_assistant_tool_call(_tool_call("call_1"))
    session.add_tool_result("call_1", _tool_result("read_file", "x" * 400))
    archiver = ToolResultArchiver(
        tmp_path,
        ContextConfig(single_tool_result_tokens=10, turn_tool_results_tokens=100),
    )

    messages, count, error = archiver.archive_large_results(session.messages)

    assert error is None
    assert count == 1
    archived_message = messages[-1]
    payload = json.loads(archived_message.content)
    assert archived_message.role == "tool"
    assert archived_message.tool_call_id == "call_1"
    assert payload["archived"] is True
    assert "archive_path" in payload
    assert "read_file" in payload["instruction"]
    archive_data = json.loads((tmp_path / payload["archive_path"]).read_text(encoding="utf-8"))
    assert archive_data["tool_result"]["output"]["content"] == "x" * 400


def test_batch_archiving_prefers_larger_tool_results(tmp_path: Path) -> None:
    messages = [
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[_tool_call("big"), _tool_call("medium"), _tool_call("small")],
        ),
        ChatMessage(role="tool", content=_tool_result("read_file", "b" * 1600), tool_call_id="big"),
        ChatMessage(role="tool", content=_tool_result("read_file", "m" * 5), tool_call_id="medium"),
        ChatMessage(role="tool", content=_tool_result("read_file", "s" * 5), tool_call_id="small"),
    ]
    archiver = ToolResultArchiver(
        tmp_path,
        ContextConfig(single_tool_result_tokens=10000, turn_tool_results_tokens=200),
    )

    updated, count, error = archiver.archive_large_results(messages)

    assert error is None
    assert count == 1
    assert json.loads(updated[1].content)["archived"] is True
    assert json.loads(updated[1].content)["tool_call_id"] == "big"
    assert "archived" not in json.loads(updated[2].content)
    assert "archived" not in json.loads(updated[3].content)


def test_small_tool_result_is_not_archived(tmp_path: Path) -> None:
    message = ChatMessage(role="tool", content=_tool_result("read_file", "small"), tool_call_id="call_1")
    archiver = ToolResultArchiver(tmp_path, ContextConfig())

    messages, count, error = archiver.archive_large_results([message])

    assert error is None
    assert count == 0
    assert messages == [message]
