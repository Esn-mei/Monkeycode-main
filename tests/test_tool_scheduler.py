from __future__ import annotations

import time
from pathlib import Path

from monkeycode.events import AgentConfig, AgentMode, CancellationToken
from monkeycode.messages import ToolCall
from monkeycode.permissions import PermissionMode
from monkeycode.tool_scheduler import ToolRoutingState, ToolScheduler
from monkeycode.tools.base import ToolContext, ToolPolicy, ToolResult
from monkeycode.tools.executor import ToolExecutor
from monkeycode.tools.registry import ToolRegistry


class RecordingTool:
    description = "Record execution."
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def __init__(self, name: str, log: list[str], policy: ToolPolicy, delay: float = 0.0) -> None:
        self.name = name
        self.policy = policy
        self._log = log
        self._delay = delay

    def execute(self, arguments, context: ToolContext) -> ToolResult:
        self._log.append(f"start:{self.name}")
        if self._delay:
            time.sleep(self._delay)
        self._log.append(f"end:{self.name}")
        return ToolResult(tool_name=self.name, success=True, output={"name": self.name})


def make_executor(tmp_path: Path, log: list[str]) -> ToolExecutor:
    registry = ToolRegistry()
    read_policy = ToolPolicy(
        tool_name="",
        category="read",
        allowed_in_plan_mode=True,
        can_run_parallel=True,
        has_side_effects=False,
    )
    write_policy = ToolPolicy(tool_name="", category="write")
    registry.register(RecordingTool("read_a", log, read_policy, delay=0.05))
    registry.register(RecordingTool("read_b", log, read_policy, delay=0.05))
    registry.register(RecordingTool("write_a", log, write_policy))
    return ToolExecutor(registry, workspace_root=tmp_path, permission_mode=PermissionMode.ALLOW)


def call(name: str, index: int) -> ToolCall:
    return ToolCall(id=f"call_{index}", name=name, arguments_json="{}", arguments={})


def call_with_arguments(name: str, index: int, arguments: dict) -> ToolCall:
    import json

    return ToolCall(
        id=f"call_{index}",
        name=name,
        arguments_json=json.dumps(arguments),
        arguments=arguments,
    )


def test_scheduler_runs_read_tools_and_preserves_result_order(tmp_path: Path) -> None:
    log: list[str] = []
    scheduler = ToolScheduler(make_executor(tmp_path, log))

    events = list(
        scheduler.run_tool_calls(
            [call("read_a", 1), call("read_b", 2)],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert [result.tool_name for _, result in scheduler.results] == ["read_a", "read_b"]
    assert [event.type for event in events].count("tool_call_started") == 2
    assert [event.type for event in events].count("tool_result") == 2


def test_scheduler_runs_side_effect_tools_serially(tmp_path: Path) -> None:
    log: list[str] = []
    scheduler = ToolScheduler(make_executor(tmp_path, log))

    list(
        scheduler.run_tool_calls(
            [call("write_a", 1), call("write_a", 2)],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert log == ["start:write_a", "end:write_a", "start:write_a", "end:write_a"]


def test_scheduler_rejects_side_effect_tools_in_plan_mode(tmp_path: Path) -> None:
    log: list[str] = []
    scheduler = ToolScheduler(make_executor(tmp_path, log))

    list(
        scheduler.run_tool_calls(
            [call("write_a", 1)],
            mode=AgentMode.PLAN,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert scheduler.results[0][1].success is False
    assert scheduler.results[0][1].error_type == "tool_not_allowed_in_plan_mode"
    assert log == []


def test_scheduler_returns_unknown_tool_result(tmp_path: Path) -> None:
    log: list[str] = []
    scheduler = ToolScheduler(make_executor(tmp_path, log))

    list(
        scheduler.run_tool_calls(
            [call("missing", 1)],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert scheduler.results[0][1].error_type == "unknown_tool"


def test_scheduler_does_not_start_when_cancelled(tmp_path: Path) -> None:
    log: list[str] = []
    token = CancellationToken()
    token.cancel()
    scheduler = ToolScheduler(make_executor(tmp_path, log))

    events = list(
        scheduler.run_tool_calls(
            [call("read_a", 1)],
            mode=AgentMode.EXECUTE,
            cancel_token=token,
            iteration=1,
        )
    )

    assert events[0].type == "cancelled"
    assert scheduler.results == []
    assert log == []


def test_scheduler_deduplicates_identical_read_calls_in_one_batch(tmp_path: Path) -> None:
    log: list[str] = []
    scheduler = ToolScheduler(make_executor(tmp_path, log))

    events = list(
        scheduler.run_tool_calls(
            [call("read_a", 1), call("read_a", 2)],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert log == ["start:read_a", "end:read_a"]
    assert len(scheduler.results) == 2
    assert scheduler.results[1][1].metadata["deduplicated"] is True
    assert scheduler.results[1][1].metadata["original_tool_call_id"] == "call_1"
    assert [event.type for event in events].count("tool_call_started") == 1


def test_scheduler_deduplicates_across_iterations_with_shared_state(tmp_path: Path) -> None:
    log: list[str] = []
    state = ToolRoutingState()
    first = ToolScheduler(make_executor(tmp_path, log), routing_state=state)
    second = ToolScheduler(make_executor(tmp_path, log), routing_state=state)

    list(
        first.run_tool_calls(
            [call("read_a", 1)],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )
    list(
        second.run_tool_calls(
            [call("read_a", 2)],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=2,
        )
    )

    assert log == ["start:read_a", "end:read_a"]
    assert second.results[0][1].metadata["deduplicated"] is True


def test_scheduler_normalizes_explicit_default_arguments(tmp_path: Path) -> None:
    from monkeycode.tools import create_default_executor

    (tmp_path / "a.py").write_text("pass", encoding="utf-8")
    scheduler = ToolScheduler(create_default_executor(tmp_path))

    list(
        scheduler.run_tool_calls(
            [
                call_with_arguments("find_files", 1, {"pattern": "*.py"}),
                call_with_arguments(
                    "find_files",
                    2,
                    {"pattern": "*.py", "max_results": 100},
                ),
            ],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert scheduler.results[1][1].metadata["deduplicated"] is True


def test_scheduler_marks_repeated_inspection_type_without_blocking(tmp_path: Path) -> None:
    log: list[str] = []
    scheduler = ToolScheduler(make_executor(tmp_path, log))

    list(
        scheduler.run_tool_calls(
            [
                call_with_arguments("read_a", 1, {"path": "a"}),
                call_with_arguments("read_a", 2, {"path": "b"}),
                call_with_arguments("read_a", 3, {"path": "c"}),
            ],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert log.count("start:read_a") == 3
    assert scheduler.results[2][1].metadata["potentially_redundant_batch"] is True
    assert "routing_advice" in scheduler.results[2][1].metadata


def test_scheduler_soft_budget_adds_feedback_without_blocking(tmp_path: Path) -> None:
    log: list[str] = []
    scheduler = ToolScheduler(
        make_executor(tmp_path, log),
        AgentConfig(soft_tool_budget=1),
    )

    list(
        scheduler.run_tool_calls(
            [
                call_with_arguments("read_a", 1, {"path": "a"}),
                call_with_arguments("read_b", 2, {"path": "b"}),
            ],
            mode=AgentMode.EXECUTE,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert log.count("start:read_a") == 1
    assert log.count("start:read_b") == 1
    assert any(
        result.metadata.get("soft_tool_budget_exceeded")
        for _, result in scheduler.results
    )
