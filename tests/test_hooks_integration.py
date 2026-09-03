from __future__ import annotations

import json
from pathlib import Path

from monkeycode.agent import AgentRunner
from monkeycode.events import AgentMode, CancellationToken
from monkeycode.hooks.engine import HookEngine
from monkeycode.hooks.types import HookActionSpec, HookConfig, HookCondition, HookMatchClause, HookRule
from monkeycode.messages import StreamEvent, ToolCall
from monkeycode.session import ChatSession
from monkeycode.tool_scheduler import ToolScheduler
from monkeycode.tools import create_default_executor
from monkeycode.tools.base import ToolContext, ToolPolicy, ToolResult
from monkeycode.tools.executor import ToolExecutor
from monkeycode.tools.registry import ToolRegistry


class RecordingTool:
    name = "record"
    description = "Record execution."
    parameters_schema = {"type": "object", "properties": {}, "required": []}
    policy = ToolPolicy(tool_name=name, category="write")

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def execute(self, arguments, context: ToolContext) -> ToolResult:
        self.log.append("executed")
        return ToolResult(tool_name=self.name, success=True, output={"ok": True})


class PromptCaptureProvider:
    def __init__(self) -> None:
        self.calls = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append(
            {
                "messages": list(messages),
                "dynamic_system_messages": list(prompt_payload.dynamic_system_messages),
            }
        )
        yield StreamEvent(type="text_delta", text="ok")
        yield StreamEvent(type="done")


class ToolProvider:
    def __init__(self, tool_call: ToolCall) -> None:
        self.tool_call = tool_call
        self.calls = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            yield StreamEvent(type="tool_call", tool_call=self.tool_call)
            yield StreamEvent(type="done")
            return
        yield StreamEvent(type="text_delta", text="saw result")
        yield StreamEvent(type="done")


def call(name: str, arguments: dict | None = None) -> ToolCall:
    arguments = arguments or {}
    return ToolCall(id="call_1", name=name, arguments_json=json.dumps(arguments), arguments=arguments)


def test_scheduler_hook_intercepts_before_tool_execution(tmp_path: Path) -> None:
    log: list[str] = []
    registry = ToolRegistry()
    registry.register(RecordingTool(log))
    executor = ToolExecutor(registry, workspace_root=tmp_path, permission_mode="allow")
    hook = HookEngine(
        HookConfig(
            rules=[
                HookRule(
                    id="block-record",
                    event="tool.before",
                    condition=HookCondition(
                        mode="all",
                        clauses=[HookMatchClause("tool.name", "record")],
                    ),
                    action=HookActionSpec(
                        type="prompt",
                        params={"target": "tool_result", "content": "blocked by hook"},
                    ),
                )
            ]
        )
    )
    scheduler = ToolScheduler(executor, hook_engine=hook)

    list(
        scheduler.run_tool_calls(
            [call("record")],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert log == []
    assert scheduler.results[0][1].error_type == "hook_intercepted"
    assert scheduler.results[0][1].error_message == "blocked by hook"


def test_scheduler_non_intercept_hook_allows_tool_execution(tmp_path: Path) -> None:
    log: list[str] = []
    registry = ToolRegistry()
    registry.register(RecordingTool(log))
    executor = ToolExecutor(registry, workspace_root=tmp_path, permission_mode="allow")
    hook = HookEngine(
        HookConfig(
            rules=[
                HookRule(
                    id="notice",
                    event="tool.before",
                    action=HookActionSpec(type="prompt", params={"content": "notice"}),
                )
            ]
        )
    )
    scheduler = ToolScheduler(executor, hook_engine=hook)

    list(
        scheduler.run_tool_calls(
            [call("record")],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert log == ["executed"]
    assert scheduler.results[0][1].success is True


def test_agent_hook_prompt_injection_reaches_provider(tmp_path: Path) -> None:
    provider = PromptCaptureProvider()
    hook = HookEngine(
        HookConfig(
            rules=[
                HookRule(
                    id="inject",
                    event="message.user_received",
                    action=HookActionSpec(type="prompt", params={"content": "HOOK_PROMPT"}),
                )
            ]
        )
    )
    runner = AgentRunner(provider, tool_executor=create_default_executor(tmp_path), hook_engine=hook)

    list(runner.run_turn("hello", ChatSession()))

    assert "HOOK_PROMPT" in "\n".join(provider.calls[0]["dynamic_system_messages"])


def test_agent_loop_continues_after_hook_intercepted_tool(tmp_path: Path) -> None:
    provider = ToolProvider(call("run_command", {"command": "python -c \"print(1)\""}))
    hook = HookEngine(
        HookConfig(
            rules=[
                HookRule(
                    id="block-command",
                    event="tool.before",
                    condition=HookCondition(
                        mode="all",
                        clauses=[
                            HookMatchClause("tool.name", "run_command"),
                            HookMatchClause("tool.arguments.command", r"print\(1\)", match="regex"),
                        ],
                    ),
                    action=HookActionSpec(
                        type="prompt",
                        params={"target": "tool_result", "content": "command blocked"},
                    ),
                )
            ]
        )
    )
    runner = AgentRunner(provider, tool_executor=create_default_executor(tmp_path), hook_engine=hook)
    session = ChatSession()

    events = list(runner.run_turn("run it", session))

    assert any(
        event.type == "tool_result"
        and event.tool_result
        and event.tool_result.error_type == "hook_intercepted"
        for event in events
    )
    assert events[-1].stop_reason == "model_done"
    assert len(provider.calls) == 2
