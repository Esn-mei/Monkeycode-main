from __future__ import annotations

import json
import shutil
import subprocess
from io import StringIO
from pathlib import Path

import pytest

from monkeycode.agent import AgentRunner
from monkeycode.config import AppConfig, SecretValue
from monkeycode.messages import StreamEvent, ToolCall
from monkeycode.permissions import PermissionRuleStore
from monkeycode.session import ChatSession
from monkeycode.tools import create_default_executor
from monkeycode.tui import run_chat_loop


class PermissionDeniedThenFinalProvider:
    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent(
                type="tool_call",
                tool_call=tool_call(
                    "write_file",
                    {"path": ".env", "content": "TOKEN=secret"},
                ),
            )
            yield StreamEvent(type="done")
            return

        assert messages[-1].role == "tool"
        assert "permission_denied" in messages[-1].content
        yield StreamEvent(type="text_delta", text="I will avoid writing .env.")
        yield StreamEvent(type="done")


class WriteThenFinalProvider:
    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent(
                type="tool_call",
                tool_call=tool_call(
                    "write_file",
                    {"path": "allowed.txt", "content": "ok"},
                ),
            )
            yield StreamEvent(type="done")
            return
        yield StreamEvent(type="text_delta", text="written")
        yield StreamEvent(type="done")


class DeniedWriteThenFinalProvider:
    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent(
                type="tool_call",
                tool_call=tool_call(
                    "write_file",
                    {"path": "denied.txt", "content": "no"},
                ),
            )
            yield StreamEvent(type="done")
            return
        assert "permission_denied" in messages[-1].content
        yield StreamEvent(type="text_delta", text="denied cleanly")
        yield StreamEvent(type="done")


def tool_call(name: str, arguments: dict, ident: str = "call_1") -> ToolCall:
    return ToolCall(
        id=ident,
        name=name,
        arguments_json=json.dumps(arguments),
        arguments=arguments,
    )


def config() -> AppConfig:
    return AppConfig(
        protocol="openai",
        model="gpt-test",
        base_url="https://example.test",
        api_key=SecretValue("sk-secret"),
        options={},
    )


def test_run_command_git_status_executes_when_rule_allows(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    rule_path = tmp_path / ".monkeycode" / "permissions.local.yaml"
    store = PermissionRuleStore.load(
        tmp_path,
        user_path=tmp_path / "missing-user.yaml",
        project_path=tmp_path / "missing-project.yaml",
        local_path=write_rule_file(
            rule_path,
            """
rules:
  run_command(git *): allow
""",
        ),
    )
    executor = create_default_executor(tmp_path, permission_rule_store=store)

    result = executor.execute(tool_call("run_command", {"command": "git status"}))

    assert result.success is True
    assert result.error_type is None
    assert result.output["exit_code"] == 0
    assert "On branch" in result.output["stdout"]
    assert result.metadata["permission_rule"] == "run_command(git *)"


def test_write_file_env_denied_by_rule_before_file_creation(tmp_path: Path) -> None:
    rule_path = tmp_path / ".monkeycode" / "permissions.local.yaml"
    store = PermissionRuleStore.load(
        tmp_path,
        user_path=tmp_path / "missing-user.yaml",
        project_path=tmp_path / "missing-project.yaml",
        local_path=write_rule_file(
            rule_path,
            """
rules:
  write_file(.env): deny
""",
        ),
    )
    executor = create_default_executor(tmp_path, permission_rule_store=store)

    result = executor.execute(tool_call("write_file", {"path": ".env", "content": "TOKEN=secret"}))

    assert result.success is False
    assert result.error_type == "permission_denied"
    assert result.metadata["permission_layer"] == "local"
    assert not (tmp_path / ".env").exists()


def test_agent_loop_continues_after_permission_denied_tool_result(tmp_path: Path) -> None:
    rule_path = tmp_path / ".monkeycode" / "permissions.local.yaml"
    store = PermissionRuleStore.load(
        tmp_path,
        user_path=tmp_path / "missing-user.yaml",
        project_path=tmp_path / "missing-project.yaml",
        local_path=write_rule_file(
            rule_path,
            """
rules:
  write_file(.env): deny
""",
        ),
    )
    provider = PermissionDeniedThenFinalProvider()
    runner = AgentRunner(provider, tool_executor=create_default_executor(tmp_path, permission_rule_store=store))

    events = list(runner.run_turn("write env", ChatSession()))

    assert provider.calls == 2
    assert any(
        event.type == "tool_result" and event.tool_result and event.tool_result.error_type == "permission_denied"
        for event in events
    )
    assert events[-1].stop_reason == "model_done"


def test_tui_permission_prompt_allows_once_and_writes_file(tmp_path: Path) -> None:
    provider = WriteThenFinalProvider()
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path),
        stdin=StringIO("write file\ny\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert "[permission] 需要确认工具调用" in output
    assert "[p]" not in output
    assert "tool: write_file" in output
    assert "allowed.txt" in output
    assert "sk-secret" not in output
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "ok"


def test_tui_permission_prompt_p_is_not_permanent_allow(tmp_path: Path) -> None:
    provider = DeniedWriteThenFinalProvider()
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path),
        stdin=StringIO("write file\np\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert "[p]" not in output
    assert "permission denied by user or non-interactive input" in output
    assert "denied cleanly" in output
    assert not (tmp_path / "denied.txt").exists()
    assert not (tmp_path / ".monkeycode" / "permissions.local.yaml").exists()


def test_tui_eof_denies_unclear_sensitive_operation(tmp_path: Path) -> None:
    provider = DeniedWriteThenFinalProvider()
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path),
        stdin=StringIO("write file\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert "permission denied by user or non-interactive input" in output
    assert "denied cleanly" in output
    assert not (tmp_path / "denied.txt").exists()


def write_rule_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
