from __future__ import annotations

import time

from monkeycode.hooks.engine import HookEngine
from monkeycode.hooks.types import (
    HookActionSpec,
    HookCondition,
    HookConfig,
    HookEventContext,
    HookExecutionControl,
    HookMatchClause,
    HookRule,
)
from monkeycode.tools.base import ToolResult


def prompt_rule(
    rule_id: str,
    content: str,
    *,
    target: str = "next_prompt",
    once: bool = False,
    condition: HookCondition | None = None,
    background: bool = False,
) -> HookRule:
    return HookRule(
        id=rule_id,
        event="turn.started",
        condition=condition,
        action=HookActionSpec(type="prompt", params={"content": content, "target": target}),
        control=HookExecutionControl(once=once, background=background),
    )


def test_dispatch_matches_and_consumes_prompt_blocks() -> None:
    engine = HookEngine(HookConfig(rules=[prompt_rule("r1", "hello")]))

    result = engine.dispatch("turn.started", {"turn": {"index": 1}})

    assert result.matched_rules == ["r1"]
    assert "hello" in "\n".join(engine.consume_prompt_blocks(turn_index=1))
    assert engine.consume_prompt_blocks(turn_index=1) == []


def test_once_rule_runs_once() -> None:
    engine = HookEngine(HookConfig(rules=[prompt_rule("r1", "hello", once=True)]))

    engine.dispatch("turn.started", {"turn": {"index": 1}})
    engine.dispatch("turn.started", {"turn": {"index": 2}})

    assert "\n".join(engine.consume_prompt_blocks(turn_index=2)).count("hello") == 1


def test_condition_must_match() -> None:
    engine = HookEngine(
        HookConfig(
            rules=[
                prompt_rule(
                    "r1",
                    "hello",
                    condition=HookCondition(mode="all", clauses=[HookMatchClause("mode", "execute")]),
                )
            ]
        )
    )

    assert engine.dispatch("turn.started", {"mode": "plan"}).matched_rules == []
    assert engine.dispatch("turn.started", {"mode": "execute"}).matched_rules == ["r1"]


def test_tool_result_prompt_blocks_tool() -> None:
    rule = HookRule(
        id="block",
        event="tool.before",
        action=HookActionSpec(type="prompt", params={"target": "tool_result", "content": "blocked"}),
    )
    engine = HookEngine(HookConfig(rules=[rule]))

    result = engine.dispatch("tool.before", {"tool": {"name": "run_command"}})

    assert result.tool_decision.allowed is False
    assert result.tool_decision.reason == "blocked"


def test_session_context_is_deduped_and_cleared() -> None:
    engine = HookEngine(HookConfig(rules=[prompt_rule("r1", "persist", target="session_context")]))

    engine.dispatch("turn.started", {"turn": {"index": 1}})
    engine.dispatch("turn.started", {"turn": {"index": 2}})

    assert "\n".join(engine.consume_prompt_blocks(turn_index=2)).count("persist") == 1
    engine.dispatch("session.cleared")
    assert engine.consume_prompt_blocks(turn_index=3) == []


def test_background_rule_does_not_block() -> None:
    engine = HookEngine(HookConfig(rules=[prompt_rule("r1", "later", background=True)]))

    started = time.monotonic()
    result = engine.dispatch("turn.started", {"turn": {"index": 1}})

    assert time.monotonic() - started < 0.5
    assert result.action_results[0].background is True
    engine.close()


def test_action_exception_does_not_escape() -> None:
    rule = HookRule(
        id="bad",
        event="turn.started",
        action=HookActionSpec(type="command", params={"command": "echo hi"}),
    )
    engine = HookEngine(HookConfig(rules=[rule]))

    result = engine.dispatch("turn.started")

    assert result.matched_rules == ["bad"]
    assert result.action_results[0].success is False


def test_subagent_launch_failure_is_reported_without_blocking_event() -> None:
    rule = HookRule(
        id="investigate",
        event="tool.error",
        action=HookActionSpec(
            type="subagent",
            params={"prompt": "investigate", "subagent_type": "missing"},
        ),
    )
    engine = HookEngine(
        HookConfig(rules=[rule]),
        subagent_launcher=lambda arguments: ToolResult(
            tool_name="Agent",
            success=False,
            error_type="unknown_subagent_type",
            error_message="unknown subagent_type: missing",
        ),
    )

    result = engine.dispatch("tool.error", {"tool": {"name": "run_command"}})

    assert result.matched_rules == ["investigate"]
    assert result.action_results[0].success is False
    assert result.action_results[0].error_message == "unknown subagent_type: missing"
    assert result.tool_decision.allowed is True
