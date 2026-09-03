from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
import json
from typing import TYPE_CHECKING, Any

from monkeycode.events import AgentConfig, AgentEvent, AgentMode, CancellationToken
from monkeycode.messages import ToolCall
from monkeycode.tools.base import ToolResult
from monkeycode.tools.executor import ToolExecutor

if TYPE_CHECKING:
    from monkeycode.hooks.engine import HookEngine


@dataclass
class _CacheEntry:
    tool_call: ToolCall
    result: ToolResult


@dataclass
class ToolRoutingState:
    cache: dict[str, _CacheEntry] = field(default_factory=dict)
    unique_read_calls: int = 0
    budget_feedback_emitted: bool = False


class ToolScheduler:
    def __init__(
        self,
        executor: ToolExecutor,
        config: AgentConfig | None = None,
        hook_engine: HookEngine | None = None,
        routing_state: ToolRoutingState | None = None,
    ) -> None:
        self.executor = executor
        self.config = config or AgentConfig()
        self.hook_engine = hook_engine
        self.routing_state = routing_state or ToolRoutingState()
        self.results: list[tuple[ToolCall, ToolResult]] = []

    def run_tool_calls(
        self,
        tool_calls: list[ToolCall],
        *,
        mode: AgentMode,
        cancel_token: CancellationToken,
        iteration: int,
        turn_index: int = 0,
    ) -> Iterator[AgentEvent]:
        self.results = []
        ordered_results: dict[int, tuple[ToolCall, ToolResult]] = {}
        advice_indices = _repeated_inspection_advice_indices(tool_calls, self.executor)

        for batch in _split_batches(tool_calls, self.executor):
            if cancel_token.cancelled:
                yield AgentEvent(
                    type="cancelled",
                    turn_index=turn_index,
                    iteration=iteration,
                    stop_reason="cancelled",
                )
                break

            runnable, deferred_duplicates = self._prepare_batch(batch, ordered_results)
            if len(runnable) > 1 and all(
                _can_run_parallel(call, self.executor, mode) for _, call in runnable
            ):
                yield from self._run_parallel(
                    runnable,
                    mode,
                    cancel_token,
                    iteration,
                    turn_index,
                    ordered_results,
                    advice_indices,
                )
            else:
                for original_index, tool_call in runnable:
                    if cancel_token.cancelled:
                        yield AgentEvent(
                            type="cancelled",
                            turn_index=turn_index,
                            iteration=iteration,
                            stop_reason="cancelled",
                        )
                        break
                    yield from self._run_one(
                        original_index,
                        tool_call,
                        mode,
                        iteration,
                        turn_index,
                        ordered_results,
                        original_index in advice_indices,
                    )
            for original_index, tool_call, source_index in deferred_duplicates:
                source_call, source_result = ordered_results[source_index]
                result = _deduplicated_result(source_call, source_result)
                ordered_results[original_index] = (tool_call, result)
                yield AgentEvent(
                    type="tool_result",
                    turn_index=turn_index,
                    iteration=iteration,
                    tool_call=tool_call,
                    tool_result=result,
                    metadata={"deduplicated": True},
                )

        self.results = [ordered_results[index] for index in sorted(ordered_results)]

    def _prepare_batch(
        self,
        batch: list[tuple[int, ToolCall]],
        ordered_results: dict[int, tuple[ToolCall, ToolResult]],
    ) -> tuple[list[tuple[int, ToolCall]], list[tuple[int, ToolCall, int]]]:
        runnable: list[tuple[int, ToolCall]] = []
        pending_keys: dict[str, int] = {}
        deferred: list[tuple[int, ToolCall, int]] = []
        for original_index, tool_call in batch:
            key = _cache_key(tool_call, self.executor)
            if key is None:
                runnable.append((original_index, tool_call))
                continue
            cached = self.routing_state.cache.get(key)
            if cached is not None:
                result = _deduplicated_result(cached.tool_call, cached.result)
                ordered_results[original_index] = (tool_call, result)
                continue
            source_index = pending_keys.get(key)
            if source_index is not None:
                deferred.append((original_index, tool_call, source_index))
                continue
            pending_keys[key] = original_index
            runnable.append((original_index, tool_call))
        return runnable, deferred

    def _run_parallel(
        self,
        batch: list[tuple[int, ToolCall]],
        mode: AgentMode,
        cancel_token: CancellationToken,
        iteration: int,
        turn_index: int,
        ordered_results: dict[int, tuple[ToolCall, ToolResult]],
        advice_indices: set[int],
    ) -> Iterator[AgentEvent]:
        max_workers = max(1, self.config.max_parallel_tools)
        for _, tool_call in batch:
            yield AgentEvent(
                type="tool_call_started",
                turn_index=turn_index,
                iteration=iteration,
                tool_call=tool_call,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._execute_or_reject, tool_call, mode, iteration, turn_index): (index, tool_call)
                for index, tool_call in batch
                if not cancel_token.cancelled
            }
            for future in as_completed(futures):
                index, tool_call = futures[future]
                result = self._record_actual(
                    tool_call,
                    future.result(),
                    add_redundancy_advice=index in advice_indices,
                )
                ordered_results[index] = (tool_call, result)
                yield AgentEvent(
                    type="tool_result",
                    turn_index=turn_index,
                    iteration=iteration,
                    tool_call=tool_call,
                    tool_result=result,
                )

    def _run_one(
        self,
        original_index: int,
        tool_call: ToolCall,
        mode: AgentMode,
        iteration: int,
        turn_index: int,
        ordered_results: dict[int, tuple[ToolCall, ToolResult]],
        add_redundancy_advice: bool,
    ) -> Iterator[AgentEvent]:
        yield AgentEvent(
            type="tool_call_started",
            turn_index=turn_index,
            iteration=iteration,
            tool_call=tool_call,
        )
        result = self._record_actual(
            tool_call,
            self._execute_or_reject(tool_call, mode, iteration, turn_index),
            add_redundancy_advice=add_redundancy_advice,
        )
        ordered_results[original_index] = (tool_call, result)
        yield AgentEvent(
            type="tool_result",
            turn_index=turn_index,
            iteration=iteration,
            tool_call=tool_call,
            tool_result=result,
        )

    def _record_actual(
        self,
        tool_call: ToolCall,
        result: ToolResult,
        *,
        add_redundancy_advice: bool,
    ) -> ToolResult:
        key = _cache_key(tool_call, self.executor)
        metadata = dict(result.metadata)
        if key is not None:
            self.routing_state.unique_read_calls += 1
            self.routing_state.cache[key] = _CacheEntry(tool_call, result)
            if (
                self.routing_state.unique_read_calls > self.config.soft_tool_budget
                and not self.routing_state.budget_feedback_emitted
            ):
                metadata["soft_tool_budget_exceeded"] = True
                metadata["routing_advice"] = (
                    "The soft inspection-tool budget was exceeded. Reuse existing results "
                    "before requesting more tools."
                )
                self.routing_state.budget_feedback_emitted = True
        if add_redundancy_advice:
            metadata["potentially_redundant_batch"] = True
            metadata["routing_advice"] = (
                "This response requested three or more calls to the same inspection tool. "
                "Prefer one well-scoped call when possible."
            )
        if metadata != result.metadata:
            result = replace(result, metadata=metadata)
            if key is not None:
                self.routing_state.cache[key] = _CacheEntry(tool_call, result)
        return result

    def _execute_or_reject(
        self,
        tool_call: ToolCall,
        mode: AgentMode,
        iteration: int,
        turn_index: int,
    ) -> ToolResult:
        if self.hook_engine is not None:
            decision = self.hook_engine.before_tool(
                tool_call,
                mode=mode,
                turn_index=turn_index,
                iteration=iteration,
            )
            if not decision.allowed:
                result = ToolResult(
                    tool_name=tool_call.name,
                    success=False,
                    error_type="hook_intercepted",
                    error_message=decision.reason or "tool call intercepted by hook",
                    metadata={"hook_rule": decision.rule_id},
                )
                self.hook_engine.after_tool(
                    "tool.error",
                    tool_call,
                    result,
                    mode=mode,
                    turn_index=turn_index,
                    iteration=iteration,
                )
                return result

        tool = self.executor.registry.get(tool_call.name)
        if tool is None:
            result = ToolResult(
                tool_name=tool_call.name,
                success=False,
                error_type="unknown_tool",
                error_message=f"unknown tool: {tool_call.name}",
            )
            self._dispatch_tool_result_hook(tool_call, result, mode, iteration, turn_index)
            return result
        if mode == AgentMode.PLAN and not self.executor.registry.policy(tool_call.name).allowed_in_plan_mode:
            result = ToolResult(
                tool_name=tool_call.name,
                success=False,
                error_type="tool_not_allowed_in_plan_mode",
                error_message=(
                    f"tool {tool_call.name} is not allowed in plan mode; "
                    "only read_file, find_files, and search_code are allowed"
                ),
            )
            self._dispatch_tool_result_hook(tool_call, result, mode, iteration, turn_index)
            return result
        result = self.executor.execute(tool_call)
        self._dispatch_tool_result_hook(tool_call, result, mode, iteration, turn_index)
        return result

    def _dispatch_tool_result_hook(
        self,
        tool_call: ToolCall,
        result: ToolResult,
        mode: AgentMode,
        iteration: int,
        turn_index: int,
    ) -> None:
        if self.hook_engine is None:
            return
        self.hook_engine.after_tool(
            "tool.after" if result.success else "tool.error",
            tool_call,
            result,
            mode=mode,
            turn_index=turn_index,
            iteration=iteration,
        )


def _split_batches(
    tool_calls: list[ToolCall],
    executor: ToolExecutor,
) -> list[list[tuple[int, ToolCall]]]:
    batches: list[list[tuple[int, ToolCall]]] = []
    current_read_batch: list[tuple[int, ToolCall]] = []
    for index, tool_call in enumerate(tool_calls):
        policy = executor.registry.policy(tool_call.name)
        if policy.can_run_parallel and not policy.has_side_effects:
            current_read_batch.append((index, tool_call))
            continue
        if current_read_batch:
            batches.append(current_read_batch)
            current_read_batch = []
        batches.append([(index, tool_call)])
    if current_read_batch:
        batches.append(current_read_batch)
    return batches


def _can_run_parallel(tool_call: ToolCall, executor: ToolExecutor, mode: AgentMode) -> bool:
    policy = executor.registry.policy(tool_call.name)
    if mode == AgentMode.PLAN and not policy.allowed_in_plan_mode:
        return False
    return policy.can_run_parallel and not policy.has_side_effects


def _cache_key(tool_call: ToolCall, executor: ToolExecutor) -> str | None:
    policy = executor.registry.policy(tool_call.name)
    if policy.has_side_effects:
        return None
    arguments = _normalized_arguments(tool_call, executor)
    return f"{tool_call.name}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"


def _normalized_arguments(tool_call: ToolCall, executor: ToolExecutor) -> dict[str, Any]:
    arguments = dict(tool_call.arguments or {})
    if not arguments and tool_call.arguments_json:
        try:
            parsed = json.loads(tool_call.arguments_json)
            if isinstance(parsed, dict):
                arguments = parsed
        except json.JSONDecodeError:
            return {"__raw__": tool_call.arguments_json}
    if tool_call.name == "read_file":
        arguments.setdefault("encoding", "utf-8")
        arguments.setdefault("max_bytes", executor.context.max_output_chars)
    elif tool_call.name == "find_files":
        arguments.setdefault("max_results", 100)
    elif tool_call.name == "search_code":
        arguments.setdefault("path_pattern", None)
        arguments.setdefault("regex", False)
        arguments.setdefault("case_sensitive", False)
        arguments.setdefault("max_results", 100)
    return arguments


def _deduplicated_result(source_call: ToolCall, source_result: ToolResult) -> ToolResult:
    return replace(
        source_result,
        metadata={
            **source_result.metadata,
            "deduplicated": True,
            "original_tool_call_id": source_call.id,
        },
    )


def _repeated_inspection_advice_indices(
    tool_calls: list[ToolCall],
    executor: ToolExecutor,
) -> set[int]:
    by_name: dict[str, list[int]] = {}
    for index, tool_call in enumerate(tool_calls):
        policy = executor.registry.policy(tool_call.name)
        if policy.has_side_effects:
            continue
        by_name.setdefault(tool_call.name, []).append(index)
    return {
        indices[-1]
        for indices in by_name.values()
        if len(indices) >= 3
    }
