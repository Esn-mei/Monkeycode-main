from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from monkeycode.hooks.actions import HookActionRunner
from monkeycode.hooks.logging import REDACTED, redact
from monkeycode.hooks.types import HookActionSpec, HookEventContext, HookRule
from monkeycode.permissions import PermissionMode
from monkeycode.tools import create_default_executor
from monkeycode.tools.base import ToolResult


def rule(action_type: str, params: dict, *, timeout: float | None = None) -> HookRule:
    from monkeycode.hooks.types import HookExecutionControl

    return HookRule(
        id="r1",
        event="turn.started",
        action=HookActionSpec(type=action_type, params=params),
        control=HookExecutionControl(timeout_seconds=timeout),
    )


def test_redact_masks_sensitive_values() -> None:
    assert redact({"Authorization": "Bearer secret", "nested": {"api_key": "sk"}}) == {
        "Authorization": REDACTED,
        "nested": {"api_key": REDACTED},
    }


def test_prompt_action() -> None:
    runner = HookActionRunner()

    prompt = runner.run(rule("prompt", {"content": "hello", "target": "next_prompt"}), HookEventContext())

    assert prompt.success is True
    assert prompt.prompt_content == "hello"


def test_subagent_action_launches_background_agent_with_safe_event_context() -> None:
    captured: list[dict] = []

    def launch(arguments: dict) -> ToolResult:
        captured.append(arguments)
        return ToolResult(
            tool_name="Agent",
            success=True,
            output={"task_id": "task_1234", "status": "async_launched"},
        )

    runner = HookActionRunner(subagent_launcher=launch)
    result = runner.run(
        rule(
            "subagent",
            {
                "prompt": "Investigate the failure",
                "subagent_type": "explore",
                "name": "hook-investigator",
                "isolation": "none",
            },
        ),
        HookEventContext.from_values(
            event={"name": "tool.error"},
            tool={
                "name": "run_command",
                "arguments": {"command": "build", "api_key": "secret"},
                "result": {"error": "failed"},
            },
        ),
    )

    assert result.success is True
    assert result.output == {
        "task_id": "task_1234",
        "status": "async_launched",
        "name": "hook-investigator",
    }
    assert captured[0]["run_in_background"] is True
    assert captured[0]["subagent_type"] == "explore"
    assert captured[0]["isolation"] == "none"
    assert "Investigate the failure" in captured[0]["prompt"]
    assert '"name": "tool.error"' in captured[0]["prompt"]
    assert REDACTED in captured[0]["prompt"]
    assert "secret" not in captured[0]["prompt"]


def test_subagent_action_limits_hook_context_size() -> None:
    captured: list[dict] = []

    def launch(arguments: dict) -> ToolResult:
        captured.append(arguments)
        return ToolResult(
            tool_name="Agent",
            success=True,
            output={"task_id": "task_1234", "status": "async_launched"},
        )

    result = HookActionRunner(subagent_launcher=launch).run(
        rule("subagent", {"prompt": "inspect"}),
        HookEventContext.from_values(tool={"result": "x" * 20000}),
    )

    assert result.success is True
    assert "...[truncated]" in captured[0]["prompt"]
    assert len(captured[0]["prompt"]) < 8200


def test_command_action_uses_tool_executor(tmp_path: Path) -> None:
    runner = HookActionRunner(
        tool_executor=create_default_executor(tmp_path, permission_mode=PermissionMode.ALLOW)
    )

    result = runner.run(rule("command", {"command": 'python -c "print(123)"'}), HookEventContext())

    assert result.success is True
    assert result.output["output"]["exit_code"] == 0
    assert "123" in result.output["output"]["stdout"]


def test_command_action_timeout_continues(tmp_path: Path) -> None:
    runner = HookActionRunner(
        tool_executor=create_default_executor(tmp_path, permission_mode=PermissionMode.ALLOW)
    )

    result = runner.run(
        rule("command", {"command": 'python -c "import time; time.sleep(1)"'}, timeout=0.1),
        HookEventContext(),
    )

    assert result.success is False
    assert result.output["error_type"] == "command_timeout"


def test_http_action_sends_request() -> None:
    received: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            received.append(self.rfile.read(length).decode("utf-8"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):  # noqa: A002
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook"
        result = HookActionRunner().run(
            rule("http", {"url": url, "method": "POST", "body": {"ok": True}}),
            HookEventContext(),
        )
    finally:
        server.shutdown()

    assert result.success is True
    assert result.output["status"] == 200
    assert received == ['{"ok": true}']


def test_action_failure_is_logged(caplog) -> None:
    caplog.set_level(logging.INFO, logger="monkeycode.hooks")

    result = HookActionRunner().run(rule("command", {"command": "echo hi"}), HookEventContext())

    assert result.success is False
    assert "command action requires a tool executor" in caplog.text
