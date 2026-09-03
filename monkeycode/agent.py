from __future__ import annotations

import inspect
import time
import json
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from monkeycode.config import ContextConfig
from monkeycode.context import ContextManager, ContextStatus
from monkeycode.errors import MonkeyCodeError
from monkeycode.events import AgentConfig, AgentEvent, AgentMode, CancellationToken
from monkeycode.instructions import InstructionBundle, load_project_instructions
from monkeycode.memory import MemoryStore, TurnSnapshot
from monkeycode.messages import ToolCall
from monkeycode.plan import DefaultPlanManager, PlanDocument, PlanManager, PlanStatus, build_replan_prompt
from monkeycode.prompting import ModeInjectionState, PromptBuilder, PromptContext, PromptModule, ProviderPromptPayload, to_provider_prompt_payload
from monkeycode.prompting import render_active_skills_block, render_skills_catalog
from monkeycode.providers.base import ChatProvider
from monkeycode.session import ChatSession
from monkeycode.session_archive import SessionArchive
from monkeycode.skills.adapter import active_to_prompt_entries, catalog_to_prompt_items
from monkeycode.skills.active import ActiveSkills
from monkeycode.skills.catalog import Catalog
from monkeycode.streaming import StreamCollector
from monkeycode.tool_scheduler import ToolRoutingState, ToolScheduler
from monkeycode.tools.base import ToolResult
from monkeycode.tools.executor import ToolExecutor

if TYPE_CHECKING:
    from monkeycode.hooks.engine import HookEngine


PLAN_TOOL_NAMES = {"read_file", "find_files", "search_code", "load_skill"}


class AgentRunner:
    def __init__(
        self,
        provider: ChatProvider,
        *,
        tool_executor: ToolExecutor | None = None,
        config: AgentConfig | None = None,
        context_config: ContextConfig | None = None,
        context_manager: ContextManager | None = None,
        session_archive: SessionArchive | None = None,
        memory_store: MemoryStore | None = None,
        user_home: Path | None = None,
        prompt_builder: PromptBuilder | None = None,
        skill_catalog: Catalog | None = None,
        active_skills: ActiveSkills | None = None,
        allowed_tool_names: list[str] | None = None,
        hook_engine: HookEngine | None = None,
        system_prompt: str | None = None,
        plan_manager: PlanManager | None = None,
        active_plan: PlanDocument | None = None,
    ) -> None:
        self.provider = provider
        self.tool_executor = tool_executor
        self.config = config or AgentConfig()
        self.prompt_builder = prompt_builder or PromptBuilder()
        self._mode_injection_state = ModeInjectionState()
        self._turn_counter = 0
        self.workspace_root = (
            self.tool_executor.context.workspace_root
            if self.tool_executor is not None
            else Path.cwd()
        )
        self.user_home = user_home or Path.home()
        self.context_manager = context_manager or ContextManager(
            provider,
            self.workspace_root,
            context_config or ContextConfig(),
        )
        self.session_archive = session_archive
        self.memory_store = memory_store
        self.skill_catalog = skill_catalog
        self.active_skills = active_skills
        self.allowed_tool_names = allowed_tool_names
        self.hook_engine = hook_engine
        self.system_prompt = system_prompt
        self.current_messages = []
        self._last_instruction_bundle = InstructionBundle(content="")
        self._memory_futures = []
        self.plan_manager = plan_manager or DefaultPlanManager()
        self.active_plan = active_plan

    def set_plan(self, plan: PlanDocument | None) -> None:
        self.active_plan = plan

    def get_plan(self) -> PlanDocument | None:
        return self.active_plan
    def run_turn(
        self,
        user_input: str,
        session: ChatSession,
        *,
        mode: AgentMode = AgentMode.EXECUTE,
        cancel_token: CancellationToken | None = None,
        turn_index: int = 0,
    ) -> Iterator[AgentEvent]:
        token = cancel_token or CancellationToken()
        turn_start_time = time.monotonic()
        effective_turn_index = turn_index
        if effective_turn_index == 0:
            self._turn_counter += 1
            effective_turn_index = self._turn_counter
        if user_input:
            session.add_user_message(user_input)
        self.current_messages = session.messages
        if self.session_archive is not None and user_input:
            self.session_archive.append_user_message(user_input)
        if user_input:
            self._dispatch_hook(
                "message.user_received",
                mode=mode,
                turn_index=effective_turn_index,
                iteration=0,
                message={"role": "user", "text": user_input},
            )
        self._dispatch_hook(
            "turn.started",
            mode=mode,
            turn_index=effective_turn_index,
            iteration=0,
            message={"role": "user", "text": user_input},
        )
        unknown_tool_streak = 0
        last_tool_results: list[tuple[ToolCall, ToolResult]] = []
        routing_state = ToolRoutingState()

        for iteration in range(1, self.config.max_iterations + 1):
            if token.cancelled:
                yield AgentEvent(
                    type="cancelled",
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    stop_reason="cancelled",
                )
                return

            yield AgentEvent(
                type="progress",
                turn_index=effective_turn_index,
                iteration=iteration,
                progress=f"iteration {iteration}",
            )

            tools = self._tool_definitions(mode)
            self.current_messages = session.messages
            prompt_payload = self._prompt_payload(
                mode=mode,
                turn_index=effective_turn_index,
                iteration=iteration,
                tools=tools or [],
            )
            self._dispatch_hook(
                "system.context_before_compact",
                mode=mode,
                turn_index=effective_turn_index,
                iteration=iteration,
            )
            context_status = self.context_manager.prepare_before_request(
                session,
                prompt_payload=prompt_payload,
                tools=tools or [],
            )
            self._dispatch_hook(
                "system.context_after_compact",
                mode=mode,
                turn_index=effective_turn_index,
                iteration=iteration,
                context={"status": _context_status_payload(context_status)},
            )
            if _should_emit_context_status(context_status):
                yield AgentEvent(
                    type="context",
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    metadata={"context_status": context_status},
                )
            collector = StreamCollector()
            try:
                events = self._stream_provider(
                    session.messages,
                    tools,
                    allow_tool_calls=bool(tools),
                    prompt_payload=prompt_payload,
                )
                for event in collector.collect(
                    events,
                    iteration=iteration,
                    turn_index=effective_turn_index,
                    emit_text_events=not last_tool_results,
                ):
                    if event.type == "usage":
                        self.context_manager.record_usage(event.usage)
                    yield event
                    if event.type == "error":
                        return
            except MonkeyCodeError as exc:
                self._dispatch_hook(
                    "turn.error",
                    mode=mode,
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                )
                yield AgentEvent(
                    type="error",
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    stop_reason="provider_error",
                )
                return
            except Exception as exc:
                self._dispatch_hook(
                    "turn.error",
                    mode=mode,
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                )
                yield AgentEvent(
                    type="error",
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    stop_reason="stream_error",
                )
                return

            response = collector.final_response
            tool_calls = [_normalize_content_count_instruction(call, user_input) for call in response.tool_calls]
            if not tool_calls:
                assistant_text = response.assistant_text
                if (
                    last_tool_results
                    and (not assistant_text.strip() or _looks_like_tool_markup(assistant_text))
                ):
                    assistant_text = _tool_result_fallback(last_tool_results[-1][1])
                if last_tool_results and assistant_text:
                    yield AgentEvent(
                        type="text_delta",
                        turn_index=effective_turn_index,
                        iteration=iteration,
                        text=assistant_text,
                    )
                session.add_assistant_message(assistant_text)
                self.current_messages = session.messages
                if self.session_archive is not None:
                    self.session_archive.append_assistant_message(assistant_text)
                self._dispatch_hook(
                    "message.assistant_completed",
                    mode=mode,
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    message={"role": "assistant", "text": assistant_text},
                )
                self._dispatch_hook(
                    "turn.completed",
                    mode=mode,
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    message={"role": "assistant", "text": assistant_text},
                )
                self._schedule_memory_update(session)
                yield AgentEvent(
                    type="done",
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    stop_reason="model_done",
                    metadata={"elapsed_ms": int((time.monotonic() - turn_start_time) * 1000)},
                )
                return

            if self.tool_executor is None:
                message = "Tool call requested, but tools are not enabled."
                session.add_assistant_message(response.assistant_text)
                self._dispatch_hook(
                    "turn.error",
                    mode=mode,
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    error={"type": "tools_not_enabled", "message": message},
                )
                yield AgentEvent(
                    type="error",
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    error_type="tools_not_enabled",
                    error_message=message,
                    stop_reason="tools_not_enabled",
                )
                return

            provider_payload = {"reasoning_content": response.reasoning_text} if response.reasoning_text else None
            session.add_assistant_tool_calls(
                tool_calls,
                content=response.assistant_text,
                provider_payload=provider_payload,
            )
            self.current_messages = session.messages
            if self.session_archive is not None:
                self.session_archive.append_assistant_tool_calls(
                    tool_calls,
                    content=response.assistant_text,
                    provider_payload=provider_payload,
                )

            scheduler = ToolScheduler(
                self.tool_executor,
                self.config,
                self.hook_engine,
                routing_state,
            )
            for event in scheduler.run_tool_calls(
                tool_calls,
                mode=mode,
                cancel_token=token,
                iteration=iteration,
                turn_index=effective_turn_index,
            ):
                if self.active_plan is not None and event.type == "tool_call_started" and event.tool_call:
                    updated_plan = self.plan_manager.mark_tool_started(
                        self.active_plan,
                        event.tool_call.id,
                    )
                    self._record_plan_checkpoint(updated_plan)
                    self.active_plan = updated_plan
                    yield AgentEvent(
                        type="plan_step",
                        turn_index=effective_turn_index,
                        iteration=iteration,
                        tool_call=event.tool_call,
                        metadata={"plan": updated_plan.to_dict()},
                    )
                yield event
                if self.active_plan is not None and event.type == "tool_result" and event.tool_call and event.tool_result:
                    updated_plan = self.plan_manager.mark_tool_result(
                        self.active_plan,
                        event.tool_call.id,
                        event.tool_result.success,
                        event.tool_result.error_message,
                    )
                    self._record_plan_checkpoint(updated_plan)
                    self.active_plan = updated_plan
                    yield AgentEvent(
                        type="plan_step",
                        turn_index=effective_turn_index,
                        iteration=iteration,
                        tool_call=event.tool_call,
                        tool_result=event.tool_result,
                        metadata={"plan": updated_plan.to_dict()},
                    )
                if event.type == "cancelled":
                    if self.active_plan is not None:
                        updated_plan = self.plan_manager.mark_interrupted(self.active_plan)
                        self._record_plan_checkpoint(updated_plan)
                        self.active_plan = updated_plan
                    return

            last_tool_results = scheduler.results
            for tool_call, result in scheduler.results:
                content = json.dumps(result.to_dict(), ensure_ascii=False)
                session.add_tool_result(tool_call.id, content)
                self.current_messages = session.messages
                if self.session_archive is not None:
                    self.session_archive.append_tool_result(tool_call.id, content)

            if scheduler.results and all(
                result.error_type == "unknown_tool" for _, result in scheduler.results
            ):
                unknown_tool_streak += 1
            else:
                unknown_tool_streak = 0

            if unknown_tool_streak >= self.config.max_consecutive_unknown_tools:
                self._dispatch_hook(
                    "turn.error",
                    mode=mode,
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    error={"type": "unknown_tool_limit", "message": "model repeatedly requested unknown tools"},
                )
                yield AgentEvent(
                    type="done",
                    turn_index=effective_turn_index,
                    iteration=iteration,
                    stop_reason="unknown_tool_limit",
                    error_type="unknown_tool_limit",
                    error_message="model repeatedly requested unknown tools",
                )
                return

        summary = _tool_result_fallback(last_tool_results[-1][1]) if last_tool_results else ""
        if summary:
            yield AgentEvent(
                type="text_delta",
                turn_index=effective_turn_index,
                iteration=self.config.max_iterations,
                text=summary,
            )
        self._dispatch_hook(
            "turn.error",
            mode=mode,
            turn_index=effective_turn_index,
            iteration=self.config.max_iterations,
            error={"type": "max_iterations", "message": "max iterations reached"},
        )
        yield AgentEvent(
            type="done",
            turn_index=effective_turn_index,
            iteration=self.config.max_iterations,
            stop_reason="max_iterations",
            progress="max iterations reached",
        )

    def run_to_completion(
        self,
        user_input: str,
        session: ChatSession,
        *,
        mode: AgentMode = AgentMode.EXECUTE,
        cancel_event=None,
        on_event=None,
    ) -> str:
        """运行到模型完成并返回最后一条 assistant 文本。"""
        token = CancellationToken()
        for event in self.run_turn(user_input, session, mode=mode, cancel_token=token):
            if cancel_event is not None and cancel_event.is_set():
                token.cancel()
            if on_event is not None:
                on_event(event)
            if event.type == "error":
                raise MonkeyCodeError(event.error_message or event.error_type or "agent error")
        for message in reversed(session.messages):
            if message.role == "assistant" and isinstance(message.content, str):
                return message.content
        return ""

    def replan_prompt(self) -> str | None:
        if self.active_plan is None or not self.plan_manager.can_replan(self.active_plan):
            return None
        failure = next(
            (step.error for step in self.active_plan.steps if step.status == PlanStatus.FAILED and step.error),
            "plan step failed",
        )
        return build_replan_prompt(self.active_plan, failure)

    def _record_plan_checkpoint(self, plan: PlanDocument) -> None:
        if self.session_archive is None or plan is self.active_plan:
            return
        self.session_archive.append_plan_checkpoint(plan)

    def _stream_provider(
        self,
        messages,
        tools,
        *,
        allow_tool_calls: bool,
        prompt_payload: ProviderPromptPayload,
    ):
        signature = inspect.signature(self.provider.stream_chat)
        parameters = signature.parameters
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        positional_parameters = [
            parameter
            for parameter in parameters.values()
            if parameter.kind
            in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }
        ]
        accepts_tools = "tools" in parameters or accepts_kwargs or len(positional_parameters) >= 2
        args = [messages]
        if tools is not None and accepts_tools:
            args.append(tools)

        kwargs = {}
        if "allow_tool_calls" in parameters or accepts_kwargs:
            kwargs["allow_tool_calls"] = allow_tool_calls
        if "prompt_payload" in parameters or accepts_kwargs:
            kwargs["prompt_payload"] = prompt_payload
        return self.provider.stream_chat(*args, **kwargs)

    def _prompt_payload(
        self,
        *,
        mode: AgentMode,
        turn_index: int,
        iteration: int,
        tools,
    ) -> ProviderPromptPayload:
        workspace_root = (
            self.tool_executor.context.workspace_root
            if self.tool_executor is not None
            else Path.cwd()
        )
        self._dispatch_hook(
            "message.prompt_before_build",
            mode=mode,
            turn_index=turn_index,
            iteration=iteration,
            prompt={"tools": [getattr(tool, "name", "") for tool in tools or []]},
        )
        rendered = self.prompt_builder.build(
            PromptContext.from_runtime(
                workspace_root=workspace_root,
                cwd=Path.cwd(),
                mode=mode,
                turn_index=turn_index,
                iteration=iteration,
                available_tools=tools,
                dynamic_context_blocks=self._dynamic_context_blocks(turn_index=turn_index),
            ),
            optional_modules=self._optional_prompt_modules(),
            injection_state=self._mode_injection_state,
        )
        self._dispatch_hook(
            "message.prompt_after_build",
            mode=mode,
            turn_index=turn_index,
            iteration=iteration,
            prompt={
                "stable_system_text": rendered.stable_system_text,
                "dynamic_messages_count": len(rendered.dynamic_system_messages),
                "tools": [getattr(tool, "name", "") for tool in tools or []],
            },
        )
        return to_provider_prompt_payload(rendered)

    def _tool_definitions(self, mode: AgentMode):
        if self.tool_executor is None:
            return None
        if self.allowed_tool_names is not None:
            return self.tool_executor.registry.definitions_filtered(self.allowed_tool_names)
        if mode == AgentMode.PLAN:
            return self.tool_executor.registry.definitions(
                allowed_names=PLAN_TOOL_NAMES,
                include_system=True,
            )
        return self.tool_executor.registry.definitions()

    def compact_now(self, session: ChatSession) -> ContextStatus:
        self._dispatch_hook(
            "system.context_before_compact",
            mode=AgentMode.EXECUTE,
            turn_index=self._turn_counter,
            iteration=0,
        )
        status = self.context_manager.compact_now(session)
        self._dispatch_hook(
            "system.context_after_compact",
            mode=AgentMode.EXECUTE,
            turn_index=self._turn_counter,
            iteration=0,
            context={"status": _context_status_payload(status)},
        )
        return status

    def _optional_prompt_modules(self) -> list[PromptModule]:
        modules: list[PromptModule] = []
        if self.system_prompt and self.system_prompt.strip():
            modules.append(PromptModule("subagent_system_prompt", 6, self.system_prompt.strip()))
        self._last_instruction_bundle = load_project_instructions(
            self.workspace_root,
            self.user_home,
        )
        if self._last_instruction_bundle.content.strip():
            modules.append(
                PromptModule(
                    "custom_instructions",
                    5,
                    "\n".join(
                        [
                            "MonkeyCode project and user instructions follow. Higher priority content appears first.",
                            self._last_instruction_bundle.content,
                        ]
                    ),
                )
            )
        if self.memory_store is not None:
            memory_index = self.memory_store.combined_index().strip()
            if memory_index:
                modules.append(
                    PromptModule(
                        "long_term_memory",
                        65,
                        "\n".join(
                            [
                                "MonkeyCode memory index follows. Treat it as background only; the current user input wins on conflict.",
                                memory_index,
                            ]
                        ),
                    )
                )
        if self.skill_catalog is not None:
            catalog_text = render_skills_catalog(catalog_to_prompt_items(self.skill_catalog))
            if catalog_text:
                modules.append(PromptModule("skills_catalog", 66, catalog_text))
        return modules

    def _dynamic_context_blocks(self, *, turn_index: int) -> list[str]:
        blocks: list[str] = []
        if self.active_skills is not None:
            active_text = render_active_skills_block(active_to_prompt_entries(self.active_skills))
            if active_text:
                blocks.append(active_text)
        if self.hook_engine is not None:
            blocks.extend(self.hook_engine.consume_prompt_blocks(turn_index=turn_index))
        return blocks

    def _dispatch_hook(
        self,
        event_name: str,
        *,
        mode: AgentMode,
        turn_index: int,
        iteration: int,
        **extra,
    ) -> None:
        if self.hook_engine is None:
            return
        context = {
            "mode": mode.value,
            "workspace_root": str(self.workspace_root),
            "cwd": str(Path.cwd()),
            "session": {"id": self.session_archive.session_id if self.session_archive is not None else ""},
            "turn": {"index": turn_index},
            "iteration": iteration,
        }
        context.update(extra)
        self.hook_engine.dispatch(event_name, context)

    def _schedule_memory_update(self, session: ChatSession) -> None:
        if self.memory_store is None or self.session_archive is None:
            return
        snapshot = TurnSnapshot(
            session_id=self.session_archive.session_id,
            messages=session.messages,
            existing_index=self.memory_store.combined_index(),
            instructions=self._last_instruction_bundle.content,
        )
        try:
            self._memory_futures.append(self.memory_store.schedule_update(self.provider, snapshot))
        except Exception:
            return


def _looks_like_tool_markup(text: str) -> bool:
    markers = ["DSML", "<tool_calls", "<invoke", "<锝渢ool_calls", "<锝渋nvoke"]
    return any(marker in text for marker in markers)


def _should_emit_context_status(status: ContextStatus) -> bool:
    return bool(status.changed or status.error_message or status.breaker_active)


def _context_status_payload(status: ContextStatus) -> dict[str, object]:
    return {
        "enabled": status.enabled,
        "archived_count": status.archived_count,
        "summary_attempted": status.summary_attempted,
        "summary_created": status.summary_created,
        "estimated_tokens": status.estimated_tokens,
        "safety_margin_tokens": status.safety_margin_tokens,
        "skipped_reason": status.skipped_reason,
        "error_message": status.error_message,
        "breaker_active": status.breaker_active,
        "changed": status.changed,
    }


def _normalize_content_count_instruction(tool_call: ToolCall, user_text: str) -> ToolCall:
    if tool_call.name not in {"write_file", "edit_file"} or not tool_call.arguments:
        return tool_call
    field = "content" if tool_call.name == "write_file" else "new_text"
    value = tool_call.arguments.get(field)
    if not isinstance(value, str):
        return tool_call

    normalized = _strip_count_suffix_if_instruction(value, user_text)
    if normalized == value:
        return tool_call

    arguments = dict(tool_call.arguments)
    arguments[field] = normalized
    return replace(
        tool_call,
        arguments=arguments,
        arguments_json=json.dumps(arguments, ensure_ascii=False),
    )


def _strip_count_suffix_if_instruction(value: str, user_text: str) -> str:
    match = re.fullmatch(
        r"(?P<content>.+?)(?:这|改成|写成|编辑)?(?P<count>[一二两三四五六七八九十\d]+)个?(?:字|字符)",
        value,
    )
    if not match:
        match = re.fullmatch(
            r"(?P<content>.+?)(?:杩??(?P<count>[涓€浜屼袱涓夊洓浜斿叚涓冨叓涔濆崄\d]+)涓??:瀛梶瀛楃)",
            value,
        )
    if not match or value not in user_text:
        return value
    content = match.group("content")
    expected_count = _count_text_to_int(match.group("count"))
    if expected_count is None or len(content) != expected_count:
        return value
    return content

def _count_text_to_int(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    values = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "涓€": 1,
        "浜?": 2,
        "涓?": 2,
        "涓夌": 3,
        "鍥?": 4,
        "浜斿": 5,
        "涓冨": 7,
        "鍏?": 8,
        "涔?": 9,
        "鍗?": 10,
    }
    return values.get(text)


def _tool_result_fallback(result: ToolResult) -> str:
    if result.success is False:
        return f"工具 {result.tool_name} 执行失败：{result.error_message or result.error_type}"

    output = result.output if isinstance(result.output, dict) else {}
    if result.tool_name == "find_files":
        files = output.get("files") or []
        if not files:
            return "没有找到匹配的文件。"
        lines = "\n".join(f"- {path}" for path in files[:50])
        suffix = "\n结果已截断。" if output.get("truncated") else ""
        return f"找到这些文件：\n{lines}{suffix}"

    if result.tool_name == "search_code":
        matches = output.get("matches") or []
        if not matches:
            return "没有找到匹配内容。"
        lines = "\n".join(
            f"- {item.get('path')}:{item.get('line')} {item.get('text', '').strip()}"
            for item in matches[:30]
        )
        suffix = "\n结果已截断。" if output.get("truncated") else ""
        return f"找到这些匹配：\n{lines}{suffix}"

    if result.tool_name == "read_file":
        path = output.get("path", "")
        content = output.get("content", "")
        return f"{path} 内容：\n{content}"

    if result.tool_name in {"write_file", "edit_file"}:
        return f"工具 {result.tool_name} 执行成功：{output}"

    if result.tool_name == "run_command":
        stdout_text = output.get("stdout", "")
        stderr_text = output.get("stderr", "")
        exit_code = output.get("exit_code")
        return f"命令退出码：{exit_code}\nstdout:\n{stdout_text}\nstderr:\n{stderr_text}"

    return f"工具 {result.tool_name} 执行成功：{output}"
