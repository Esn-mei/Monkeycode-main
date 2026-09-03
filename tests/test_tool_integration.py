from __future__ import annotations

from io import StringIO

from monkeycode.config import AppConfig, SecretValue
from monkeycode.messages import StreamEvent, ToolCall
from monkeycode.permissions import PermissionMode
from monkeycode.tools import create_default_executor
from monkeycode.tui import run_chat_loop


class ToolProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.allow_tool_calls = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True):
        self.calls += 1
        self.allow_tool_calls.append(allow_tool_calls)
        if self.calls == 1:
            assert tools
            assert allow_tool_calls is True
            yield StreamEvent(type="reasoning_delta", text="Need the README.")
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id="call_1",
                    name="read_file",
                    arguments_json='{"path":"README.md"}',
                    arguments={"path": "README.md"},
                    provider="test",
                ),
            )
            yield StreamEvent(type="done")
        else:
            assert tools
            assert allow_tool_calls is True
            assert messages[-1].role == "tool"
            assert messages[-2].provider_payload == {"reasoning_content": "Need the README."}
            yield StreamEvent(type="text_delta", text="Read it.")
            yield StreamEvent(type="done")


class RepeatingToolProvider(ToolProvider):
    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True):
        self.calls += 1
        self.allow_tool_calls.append(allow_tool_calls)
        yield StreamEvent(
            type="tool_call",
            tool_call=ToolCall(
                id=f"call_{self.calls}",
                name="read_file",
                arguments_json='{"path":"README.md"}',
                arguments={"path": "README.md"},
                provider="test",
            ),
        )
        yield StreamEvent(type="done")


class MarkupFinalProvider(ToolProvider):
    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True):
        self.calls += 1
        self.allow_tool_calls.append(allow_tool_calls)
        if self.calls == 1:
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id="call_1",
                    name="find_files",
                    arguments_json='{"pattern":"*.py"}',
                    arguments={"pattern": "*.py"},
                    provider="test",
                ),
            )
            yield StreamEvent(type="done")
        else:
            yield StreamEvent(type="text_delta", text="<｜DSML｜tool_calls>more tools</｜DSML｜tool_calls>")
            yield StreamEvent(type="done")


class FailThenRecoverProvider(ToolProvider):
    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True):
        self.calls += 1
        self.allow_tool_calls.append(allow_tool_calls)
        if self.calls == 1:
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id="call_1",
                    name="run_command",
                    arguments_json='{"command":"python -c \\"import sys; sys.exit(1)\\""}',
                    arguments={"command": "python -c \"import sys; sys.exit(1)\""},
                    provider="test",
                ),
            )
            yield StreamEvent(type="done")
        elif self.calls == 2:
            assert messages[-1].role == "tool"
            assert "command_failed" in str(messages[-1].content)
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id="call_2",
                    name="find_files",
                    arguments_json='{"pattern":"*.py"}',
                    arguments={"pattern": "*.py"},
                    provider="test",
                ),
            )
            yield StreamEvent(type="done")
        else:
            assert messages[-1].role == "tool"
            yield StreamEvent(type="text_delta", text="The entry file is monkeycode/__main__.py")
            yield StreamEvent(type="done")


class EditCountInstructionProvider(ToolProvider):
    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True):
        self.calls += 1
        self.allow_tool_calls.append(allow_tool_calls)
        if self.calls == 1:
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id="call_1",
                    name="edit_file",
                    arguments_json=(
                        '{"path":"note.txt","old_text":"这是测试文件","new_text":"编辑两个字"}'
                    ),
                    arguments={
                        "path": "note.txt",
                        "old_text": "这是测试文件",
                        "new_text": "编辑两个字",
                    },
                    provider="test",
                ),
            )
            yield StreamEvent(type="done")
        else:
            yield StreamEvent(type="text_delta", text="done")
            yield StreamEvent(type="done")


def config() -> AppConfig:
    return AppConfig(
        protocol="openai",
        model="gpt-test",
        base_url="https://example.test",
        api_key=SecretValue("sk-secret"),
        options={},
    )


def test_tui_executes_one_tool_round_and_streams_final_reply(tmp_path) -> None:
    (tmp_path / "README.md").write_text("MonkeyCode", encoding="utf-8")
    provider = ToolProvider()
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path, permission_mode=PermissionMode.ALLOW),
        stdin=StringIO("read readme\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == 2
    assert provider.allow_tool_calls == [True, True]
    assert "[tool] read_file running" in output
    assert "[tool] read_file done" in output
    assert "\n  \x1b[90m[tool] read_file running...\x1b[0m\n" in output
    assert "  \x1b[90m[tool] read_file done\x1b[0m\n" in output
    assert "Read it." in output
    assert "sk-secret" not in output


def test_tui_stops_repeating_tool_loop_at_iteration_limit(tmp_path) -> None:
    (tmp_path / "README.md").write_text("MonkeyCode", encoding="utf-8")
    provider = RepeatingToolProvider()
    stdout = StringIO()

    run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path, permission_mode=PermissionMode.ALLOW),
        stdin=StringIO("read readme\nquit\n"),
        stdout=stdout,
    )

    assert provider.calls == 10
    output = stdout.getvalue()
    assert output.count("[tool] read_file running") == 1
    assert output.count("[tool] read_file done") == 1
    assert "single-tool stage stops here" not in output
    assert "README.md" in output
    assert "MonkeyCode" in output
    assert "max iterations" in output


def test_tui_falls_back_to_tool_result_when_final_response_is_tool_markup(tmp_path) -> None:
    (tmp_path / "app.py").write_text("print('app')", encoding="utf-8")
    (tmp_path / "monkeycode.py").write_text("print('monkey')", encoding="utf-8")
    provider = MarkupFinalProvider()
    stdout = StringIO()

    run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path, permission_mode=PermissionMode.ALLOW),
        stdin=StringIO("find python files\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert "找到这些文件" in output
    assert "app.py" in output
    assert "monkeycode.py" in output
    assert "DSML" not in output


def test_tui_continues_after_tool_failure_until_final_answer(tmp_path) -> None:
    (tmp_path / "main.py").write_text("print('main')", encoding="utf-8")
    provider = FailThenRecoverProvider()
    stdout = StringIO()

    run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path, permission_mode=PermissionMode.ALLOW),
        stdin=StringIO("find entry\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert provider.calls == 3
    assert "[tool] run_command failed" in output
    assert "[tool] find_files done" in output
    assert "monkeycode/__main__.py" in output


def test_tui_strips_chinese_count_instruction_from_edit_content(tmp_path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("这是测试文件", encoding="utf-8")
    provider = EditCountInstructionProvider()

    run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path, permission_mode=PermissionMode.ALLOW),
        stdin=StringIO("用editfile把note的内容改成编辑两个字\nquit\n"),
        stdout=StringIO(),
    )

    assert note.read_text(encoding="utf-8") == "编辑"
