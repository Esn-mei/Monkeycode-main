from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from monkeycode.events import AgentMode
from monkeycode.hooks.actions import HookActionRunner
from monkeycode.hooks.logging import log_hook_event
from monkeycode.hooks.matcher import match_condition
from monkeycode.hooks.types import (
    HookActionResult,
    HookConfig,
    HookDispatchResult,
    HookEventContext,
    HookRule,
    HookToolDecision,
)
from monkeycode.messages import ToolCall
from monkeycode.tools.base import ToolResult


class HookEngine:
    def __init__(
        self,
        config: HookConfig | None = None,
        *,
        tool_executor: Any = None,
        subagent_launcher: Any = None,
        logger: logging.Logger | None = None,
        max_workers: int = 4,
    ) -> None:
        self.config = config or HookConfig()
        self.logger = logger or logging.getLogger("monkeycode.hooks")
        self.runner = HookActionRunner(
            tool_executor=tool_executor,
            subagent_launcher=subagent_launcher,
            logger=self.logger,
        )
        self._once_executed: set[str] = set()
        self._prompt_buffer = HookPromptBuffer()
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max(1, max_workers))

    @property
    def is_empty(self) -> bool:
        return not self.config.rules

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def dispatch(self, event_name: str, context: HookEventContext | dict[str, Any] | None = None) -> HookDispatchResult:
        event_context = self._event_context(event_name, context)
        matched: list[str] = []
        action_results: list[HookActionResult] = []
        decision = HookToolDecision.allow()

        for rule in self._rules_for(event_name):
            try:
                if not match_condition(rule.condition, event_context):
                    continue
                if self._already_ran_once(rule):
                    continue
                matched.append(rule.id)
                self._mark_once(rule)
                if rule.control.background:
                    self._submit_background(rule, event_context)
                    action_results.append(
                        HookActionResult(
                            success=True,
                            action_type=rule.action.type,
                            output={"background": True},
                            background=True,
                        )
                    )
                    continue
                result = self.runner.run(rule, event_context)
                action_results.append(result)
                block_decision = self._apply_action_result(rule, result, event_context)
                if decision.allowed and block_decision is not None:
                    decision = block_decision
            except Exception as exc:
                log_hook_event(
                    self.logger,
                    level=logging.WARNING,
                    event=event_name,
                    rule_id=rule.id,
                    action_type=rule.action.type,
                    status="failed",
                    reason=f"{exc.__class__.__name__}: {exc}",
                )

        if event_name in {"turn.completed", "turn.error"}:
            turn = event_context.data.get("turn", {})
            if isinstance(turn, dict):
                self._prompt_buffer.clear_turn(turn.get("index"))
        if event_name in {"session.ended", "session.cleared"}:
            self._prompt_buffer.clear_session()

        return HookDispatchResult(
            event=event_name,
            matched_rules=matched,
            action_results=action_results,
            tool_decision=decision,
        )

    def before_tool(
        self,
        tool_call: ToolCall,
        *,
        mode: AgentMode,
        turn_index: int,
        iteration: int,
    ) -> HookToolDecision:
        context = {
            "mode": mode.value,
            "turn": {"index": turn_index},
            "iteration": iteration,
            "tool": {
                "id": tool_call.id,
                "name": tool_call.name,
                "arguments": tool_call.arguments or {},
                "arguments_json": tool_call.arguments_json,
                "provider": tool_call.provider,
            },
        }
        return self.dispatch("tool.before", context).tool_decision

    def after_tool(
        self,
        event_name: str,
        tool_call: ToolCall,
        result: ToolResult,
        *,
        mode: AgentMode,
        turn_index: int,
        iteration: int,
    ) -> HookDispatchResult:
        return self.dispatch(
            event_name,
            {
                "mode": mode.value,
                "turn": {"index": turn_index},
                "iteration": iteration,
                "tool": {
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "arguments": tool_call.arguments or {},
                    "arguments_json": tool_call.arguments_json,
                    "provider": tool_call.provider,
                    "result": result.to_dict(),
                },
            },
        )

    def consume_prompt_blocks(self, *, turn_index: int | None = None) -> list[str]:
        return self._prompt_buffer.consume(turn_index=turn_index)

    def _rules_for(self, event_name: str) -> list[HookRule]:
        return [rule for rule in self.config.rules if rule.event == event_name]

    def _event_context(
        self,
        event_name: str,
        context: HookEventContext | dict[str, Any] | None,
    ) -> HookEventContext:
        data = dict(context.data) if isinstance(context, HookEventContext) else dict(context or {})
        event = data.get("event")
        if not isinstance(event, dict):
            event = {}
        event = {**event, "name": event_name}
        data["event"] = event
        return HookEventContext(data=data)

    def _already_ran_once(self, rule: HookRule) -> bool:
        with self._lock:
            return rule.control.once and rule.id in self._once_executed

    def _mark_once(self, rule: HookRule) -> None:
        if not rule.control.once:
            return
        with self._lock:
            self._once_executed.add(rule.id)

    def _apply_action_result(
        self,
        rule: HookRule,
        result: HookActionResult,
        context: HookEventContext,
    ) -> HookToolDecision | None:
        if not result.success:
            return None
        if result.prompt_content is None:
            return None
        target = result.prompt_target or "next_prompt"
        if rule.event == "tool.before" and target == "tool_result":
            return HookToolDecision.block(result.prompt_content, rule.id)
        turn_index = _turn_index(context)
        self._prompt_buffer.add(rule.id, target, result.prompt_content, turn_index=turn_index)
        return None

    def _submit_background(self, rule: HookRule, context: HookEventContext) -> None:
        def run() -> None:
            try:
                result = self.runner.run(rule, context)
                self._apply_action_result(rule, result, context)
            except Exception as exc:
                log_hook_event(
                    self.logger,
                    level=logging.WARNING,
                    event=rule.event,
                    rule_id=rule.id,
                    action_type=rule.action.type,
                    status="failed",
                    reason=f"{exc.__class__.__name__}: {exc}",
                )

        self._pool.submit(run)


class HookPromptBuffer:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._next_prompt: list[tuple[str, str]] = []
        self._turn_context: dict[int | None, list[tuple[str, str]]] = {}
        self._session_context: dict[tuple[str, str], str] = {}

    def add(self, rule_id: str, target: str, content: str, *, turn_index: int | None = None) -> None:
        text = content.strip()
        if not text:
            return
        with self._lock:
            if target == "session_context":
                self._session_context[(rule_id, text)] = text
            elif target == "turn_context":
                self._turn_context.setdefault(turn_index, [])
                item = (rule_id, text)
                if item not in self._turn_context[turn_index]:
                    self._turn_context[turn_index].append(item)
            else:
                self._next_prompt.append((rule_id, text))

    def consume(self, *, turn_index: int | None = None) -> list[str]:
        with self._lock:
            blocks: list[str] = []
            session_items = list(self._session_context.values())
            turn_items = [text for _, text in self._turn_context.get(turn_index, [])]
            next_items = [text for _, text in self._next_prompt]
            self._next_prompt.clear()
        for title, items in [
            ("Session Hook Context", session_items),
            ("Turn Hook Context", turn_items),
            ("Hook Context", next_items),
        ]:
            if items:
                blocks.append(_render_block(title, items))
        return blocks

    def clear_turn(self, turn_index: Any) -> None:
        with self._lock:
            try:
                key = int(turn_index) if turn_index is not None else None
            except (TypeError, ValueError):
                key = None
            self._turn_context.pop(key, None)

    def clear_session(self) -> None:
        with self._lock:
            self._next_prompt.clear()
            self._turn_context.clear()
            self._session_context.clear()


def _render_block(title: str, items: list[str]) -> str:
    lines = [f"## {title}"]
    for item in items:
        lines.extend(["", item.strip()])
    return "\n".join(lines)


def _turn_index(context: HookEventContext) -> int | None:
    turn = context.data.get("turn")
    if not isinstance(turn, dict):
        return None
    value = turn.get("index")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
