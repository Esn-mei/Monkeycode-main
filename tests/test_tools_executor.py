from __future__ import annotations

from pathlib import Path
import time

from monkeycode.messages import ToolCall
from monkeycode.permissions import PermissionMode
from monkeycode.tools.base import ToolContext, ToolResult
from monkeycode.tools.commands import RunCommandTool
from monkeycode.tools.executor import ToolExecutor
from monkeycode.tools.registry import ToolRegistry


class EchoTool:
    name = "echo"
    description = "Echo text."
    parameters_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def execute(self, arguments, context: ToolContext) -> ToolResult:
        return ToolResult(tool_name=self.name, success=True, output={"text": arguments["text"]})


class BoomTool(EchoTool):
    name = "boom"

    def execute(self, arguments, context: ToolContext) -> ToolResult:
        raise RuntimeError("boom")


class SlowTool(EchoTool):
    name = "slow"

    def execute(self, arguments, context: ToolContext) -> ToolResult:
        time.sleep(1)
        return ToolResult(tool_name=self.name, success=True)


def executor(tmp_path: Path) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return ToolExecutor(registry, workspace_root=tmp_path, permission_mode=PermissionMode.ALLOW)


def call(name: str, arguments_json: str) -> ToolCall:
    return ToolCall(id="call_1", name=name, arguments_json=arguments_json)


def test_register_rejects_duplicate_tool_names() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    try:
        registry.register(EchoTool())
    except ValueError as exc:
        assert "duplicate tool name" in str(exc)
    else:
        raise AssertionError("duplicate tool was accepted")


def test_registry_count() -> None:
    registry = ToolRegistry()
    assert registry.count() == 0
    registry.register(EchoTool())
    assert registry.count() == 1


def test_executor_runs_registered_tool(tmp_path: Path) -> None:
    result = executor(tmp_path).execute(call("echo", '{"text":"hi"}'))

    assert result.success is True
    assert result.output == {"text": "hi"}


def test_executor_returns_structured_errors(tmp_path: Path) -> None:
    tool_executor = executor(tmp_path)

    assert tool_executor.execute(call("missing", "{}")).error_type == "unknown_tool"
    assert tool_executor.execute(call("echo", "{")).error_type == "invalid_json"
    assert tool_executor.execute(call("echo", "[]")).error_type == "invalid_arguments"
    assert tool_executor.execute(call("echo", "{}")).error_type == "schema_validation_failed"
    assert tool_executor.execute(call("echo", '{"text":1}')).error_type == "schema_validation_failed"


def test_executor_wraps_tool_exception(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(BoomTool())
    tool_executor = ToolExecutor(registry, workspace_root=tmp_path, permission_mode=PermissionMode.ALLOW)

    result = tool_executor.execute(call("boom", '{"text":"hi"}'))

    assert result.success is False
    assert result.error_type == "tool_exception"


def test_executor_applies_generic_tool_timeout(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(SlowTool())
    tool_executor = ToolExecutor(
        registry,
        workspace_root=tmp_path,
        default_timeout_seconds=0.01,
        permission_mode=PermissionMode.ALLOW,
    )

    result = tool_executor.execute(call("slow", '{"text":"hi"}'))

    assert result.success is False
    assert result.error_type == "tool_timeout"


def test_run_command_captures_success_failure_and_timeout(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(RunCommandTool())
    tool_executor = ToolExecutor(
        registry,
        workspace_root=tmp_path,
        default_timeout_seconds=2,
        permission_mode=PermissionMode.ALLOW,
    )

    success = tool_executor.execute(call("run_command", '{"command":"python -c \\"print(123)\\""}'))
    failure = tool_executor.execute(call("run_command", '{"command":"python -c \\"import sys; sys.exit(3)\\""}'))
    timeout = tool_executor.execute(
        call("run_command", '{"command":"python -c \\"import time; time.sleep(1)\\"","timeout_seconds":0.1}')
    )

    assert success.success is True
    assert "123" in success.output["stdout"]
    assert success.output["timed_out"] is False
    assert failure.success is False
    assert failure.output["exit_code"] == 3
    assert timeout.success is False
    assert timeout.error_type == "command_timeout"
    assert timeout.output["timed_out"] is True


def test_run_command_replaces_invalid_output_bytes(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(RunCommandTool())
    tool_executor = ToolExecutor(
        registry,
        workspace_root=tmp_path,
        permission_mode=PermissionMode.ALLOW,
    )
    (tmp_path / "emit_bytes.py").write_text(
        "import sys\nsys.stdout.buffer.write(bytes([255]))\n",
        encoding="utf-8",
    )

    result = tool_executor.execute(call("run_command", '{"command":"python emit_bytes.py"}'))

    assert result.success is True
    assert "�" in result.output["stdout"]
