from __future__ import annotations

import inspect
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from monkeycode.config import ContextConfig
from monkeycode.errors import ConfigError
from monkeycode.messages import ChatMessage, StreamEvent, ToolCall, ToolDefinition
from monkeycode.prompting import ProviderPromptPayload
from monkeycode.session import ChatSession
from monkeycode.tools.workspace import WorkspaceError, WorkspaceGuard


SUMMARY_STABLE_PROMPT = "\n".join(
    [
        "You are MonkeyCode's internal context compactor.",
        "You must not call tools. Produce only the final structured summary.",
        "If you need to reason, write an internal analysis draft first and discard it.",
    ]
)

BOUNDARY_MESSAGE = (
    "MonkeyCode context boundary: earlier conversation was compressed. "
    "Summaries and tool previews may omit exact file content or command output. "
    "When code, file, or archived tool details matter, call read_file on the source file "
    "or archive_path before acting. Do not invent code from the summary."
)


@dataclass(frozen=True)
class ContextStatus:
    enabled: bool = True
    archived_count: int = 0
    summary_attempted: bool = False
    summary_created: bool = False
    estimated_tokens: int = 0
    safety_margin_tokens: int = 0
    skipped_reason: str | None = None
    error_message: str | None = None
    breaker_active: bool = False

    @property
    def changed(self) -> bool:
        return self.archived_count > 0 or self.summary_created


class TokenEstimator:
    def __init__(self) -> None:
        self._anchor_tokens: int | None = None
        self._anchor_chars: int | None = None

    def record_usage(self, usage: dict[str, Any] | None, *, request_chars: int | None = None) -> None:
        token_count = _usage_token_count(usage)
        if token_count is None:
            return
        self._anchor_tokens = token_count
        self._anchor_chars = request_chars

    def request_char_count(
        self,
        messages: list[ChatMessage],
        prompt_payload: ProviderPromptPayload | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> int:
        parts: list[str] = []
        if prompt_payload is not None:
            parts.append(prompt_payload.stable_system_text)
            parts.extend(prompt_payload.dynamic_system_messages)
        for tool in tools or []:
            parts.append(_json_dumps(asdict(tool)))
        for message in messages:
            parts.append(_json_dumps(_message_payload(message)))
        return sum(len(part) for part in parts)

    def estimate_request_tokens(
        self,
        messages: list[ChatMessage],
        prompt_payload: ProviderPromptPayload | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> int:
        char_count = self.request_char_count(messages, prompt_payload, tools)
        char_estimate = estimate_text_tokens("x" * char_count)
        if self._anchor_tokens is None:
            return char_estimate
        if self._anchor_chars is None:
            return max(self._anchor_tokens, char_estimate)
        delta_chars = max(0, char_count - self._anchor_chars)
        return self._anchor_tokens + estimate_text_tokens("x" * delta_chars)

    def estimate_messages_tokens(self, messages: list[ChatMessage]) -> int:
        return sum(estimate_message_tokens(message) for message in messages)


class ToolResultArchiver:
    def __init__(self, workspace_root: Path, config: ContextConfig) -> None:
        self.workspace_root = workspace_root.resolve()
        self.config = config
        try:
            self.archive_base = WorkspaceGuard(self.workspace_root).resolve(config.archive_dir) / "archives"
        except WorkspaceError as exc:
            raise ConfigError(f"context.archive_dir must stay inside workspace: {exc}") from exc

    def archive_large_results(self, messages: list[ChatMessage]) -> tuple[list[ChatMessage], int, str | None]:
        updated = list(messages)
        archived_count = 0
        try:
            for index, message in enumerate(list(updated)):
                if not _is_unarchived_tool_message(message):
                    continue
                if estimate_message_tokens(message) > self.config.single_tool_result_tokens:
                    updated[index] = self._archive_message(message)
                    archived_count += 1

            for group in _tool_message_groups(updated):
                candidates = [
                    (index, estimate_message_tokens(updated[index]))
                    for index in group
                    if _is_unarchived_tool_message(updated[index])
                ]
                total = sum(size for _, size in candidates)
                if total <= self.config.turn_tool_results_tokens:
                    continue
                for index, size in sorted(candidates, key=lambda item: item[1], reverse=True):
                    updated[index] = self._archive_message(updated[index])
                    archived_count += 1
                    total -= size
                    if total <= self.config.turn_tool_results_tokens:
                        break
        except OSError as exc:
            return messages, archived_count, f"{exc.__class__.__name__}: {exc}"
        return updated, archived_count, None

    def _archive_message(self, message: ChatMessage) -> ChatMessage:
        self.archive_base.mkdir(parents=True, exist_ok=True)
        parsed = _parse_json_object(message.content)
        tool_name = _tool_name_from_result(parsed)
        filename = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}.json"
        archive_path = self.archive_base / filename
        record = {
            "archived_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool_call_id": message.tool_call_id,
            "message": _message_payload(message),
            "tool_result": parsed,
            "raw_content": message.content if isinstance(message.content, str) else message.content,
        }
        archive_path.write_text(_json_dumps(record), encoding="utf-8")
        relative_path = archive_path.relative_to(self.workspace_root).as_posix()
        preview = _preview_text(message.content)
        compressed = {
            "archived": True,
            "archive_path": relative_path,
            "tool_call_id": message.tool_call_id,
            "tool_name": tool_name,
            "success": parsed.get("success") if parsed else None,
            "error_type": parsed.get("error_type") if parsed else None,
            "error_message": parsed.get("error_message") if parsed else None,
            "preview": preview,
            "instruction": (
                f"完整工具结果已归档。如需细节，请调用 read_file 读取 {relative_path}。"
                "不要凭预览或摘要脑补代码。"
            ),
        }
        return ChatMessage(
            role="tool",
            content=_json_dumps(compressed),
            tool_call_id=message.tool_call_id,
        )


class ConversationCompactor:
    def __init__(self, provider: Any, config: ContextConfig, estimator: TokenEstimator) -> None:
        self.provider = provider
        self.config = config
        self.estimator = estimator
        self.failure_count = 0

    @property
    def breaker_active(self) -> bool:
        return self.failure_count >= 3

    def compact_if_needed(
        self,
        session: ChatSession,
        *,
        safety_margin_tokens: int,
        prompt_payload: ProviderPromptPayload | None = None,
        tools: list[ToolDefinition] | None = None,
        force_attempt: bool = False,
    ) -> ContextStatus:
        messages = session.messages
        estimated = self.estimator.estimate_request_tokens(messages, prompt_payload, tools)
        limit = self.config.context_window_tokens - safety_margin_tokens
        if self.breaker_active:
            return ContextStatus(
                estimated_tokens=estimated,
                safety_margin_tokens=safety_margin_tokens,
                skipped_reason="summary_breaker_active",
                breaker_active=True,
            )
        if not force_attempt and estimated <= limit:
            return ContextStatus(
                estimated_tokens=estimated,
                safety_margin_tokens=safety_margin_tokens,
                skipped_reason="within_budget",
            )

        split_index = _tail_split_index(messages, self.config.recent_tail_tokens, self.config.recent_tail_min_messages)
        if split_index <= 0:
            return ContextStatus(
                estimated_tokens=estimated,
                safety_margin_tokens=safety_margin_tokens,
                skipped_reason="nothing_to_summarize",
            )

        older = messages[:split_index]
        tail = messages[split_index:]
        try:
            summary = self._summarize(older)
        except Exception as exc:
            self.failure_count += 1
            return ContextStatus(
                summary_attempted=True,
                estimated_tokens=estimated,
                safety_margin_tokens=safety_margin_tokens,
                skipped_reason="summary_failed",
                error_message=f"{exc.__class__.__name__}: {exc}",
                breaker_active=self.breaker_active,
            )

        if not summary.strip():
            self.failure_count += 1
            return ContextStatus(
                summary_attempted=True,
                estimated_tokens=estimated,
                safety_margin_tokens=safety_margin_tokens,
                skipped_reason="empty_summary",
                breaker_active=self.breaker_active,
            )

        self.failure_count = 0
        session.replace_messages(
            [
                ChatMessage(role="user", content=summary.strip()),
                ChatMessage(role="user", content=BOUNDARY_MESSAGE),
                *tail,
            ]
        )
        new_estimate = self.estimator.estimate_request_tokens(session.messages, prompt_payload, tools)
        return ContextStatus(
            summary_attempted=True,
            summary_created=True,
            estimated_tokens=new_estimate,
            safety_margin_tokens=safety_margin_tokens,
        )

    def _summarize(self, messages: list[ChatMessage]) -> str:
        summary_messages = [
            ChatMessage(
                role="user",
                content=_summary_prompt(messages),
            )
        ]
        prompt_payload = ProviderPromptPayload(stable_system_text=SUMMARY_STABLE_PROMPT)
        text_parts: list[str] = []
        for event in _stream_provider_without_tools(self.provider, summary_messages, prompt_payload):
            if event.type == "text_delta" and event.text:
                text_parts.append(event.text)
            elif event.type == "tool_call":
                raise RuntimeError("summary provider attempted to call a tool")
            elif event.type == "error":
                raise RuntimeError(event.error_message or event.error_type or "summary provider error")
        return "".join(text_parts)


class ContextManager:
    def __init__(
        self,
        provider: Any,
        workspace_root: Path,
        config: ContextConfig | None = None,
    ) -> None:
        self.provider = provider
        self.workspace_root = workspace_root
        self.config = config or ContextConfig()
        self.estimator = TokenEstimator()
        self.archiver = ToolResultArchiver(workspace_root, self.config)
        self.compactor = ConversationCompactor(provider, self.config, self.estimator)
        self.last_status = ContextStatus(enabled=self.config.enabled)
        self._last_request_chars: int | None = None

    def prepare_before_request(
        self,
        session: ChatSession,
        *,
        prompt_payload: ProviderPromptPayload | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> ContextStatus:
        status = self._prepare(
            session,
            safety_margin_tokens=self.config.auto_safety_margin_tokens,
            prompt_payload=prompt_payload,
            tools=tools,
            force_summary=False,
        )
        self._last_request_chars = self.estimator.request_char_count(session.messages, prompt_payload, tools)
        return status

    def compact_now(
        self,
        session: ChatSession,
        *,
        prompt_payload: ProviderPromptPayload | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> ContextStatus:
        return self._prepare(
            session,
            safety_margin_tokens=self.config.manual_safety_margin_tokens,
            prompt_payload=prompt_payload,
            tools=tools,
            force_summary=False,
        )

    def record_usage(self, usage: dict[str, Any] | None) -> None:
        self.estimator.record_usage(usage, request_chars=self._last_request_chars)

    def format_status(self, status: ContextStatus | None = None) -> str | None:
        current = status or self.last_status
        if not current.enabled:
            return None
        parts: list[str] = []
        if current.archived_count:
            parts.append(f"archived={current.archived_count}")
        if current.summary_created:
            parts.append("summary=created")
        elif current.breaker_active:
            parts.append("summary=breaker")
        elif current.summary_attempted and current.error_message:
            parts.append("summary=failed")
        if current.estimated_tokens:
            parts.append(f"estimated={current.estimated_tokens}")
        if not parts:
            return None
        return " ".join(parts)

    def _prepare(
        self,
        session: ChatSession,
        *,
        safety_margin_tokens: int,
        prompt_payload: ProviderPromptPayload | None,
        tools: list[ToolDefinition] | None,
        force_summary: bool,
    ) -> ContextStatus:
        if not self.config.enabled:
            self.last_status = ContextStatus(enabled=False, skipped_reason="disabled")
            return self.last_status

        archived_messages, archived_count, archive_error = self.archiver.archive_large_results(session.messages)
        if archived_count and archive_error is None:
            session.replace_messages(archived_messages)

        summary_status = self.compactor.compact_if_needed(
            session,
            safety_margin_tokens=safety_margin_tokens,
            prompt_payload=prompt_payload,
            tools=tools,
            force_attempt=force_summary,
        )
        self.last_status = ContextStatus(
            enabled=True,
            archived_count=archived_count if archive_error is None else 0,
            summary_attempted=summary_status.summary_attempted,
            summary_created=summary_status.summary_created,
            estimated_tokens=summary_status.estimated_tokens,
            safety_margin_tokens=safety_margin_tokens,
            skipped_reason=summary_status.skipped_reason,
            error_message=archive_error or summary_status.error_message,
            breaker_active=summary_status.breaker_active,
        )
        return self.last_status


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_message_tokens(message: ChatMessage) -> int:
    return estimate_text_tokens(_json_dumps(_message_payload(message)))


def _tool_message_groups(messages: list[ChatMessage]) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    for index, message in enumerate(messages):
        if message.role == "tool":
            current.append(index)
            continue
        if current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _is_unarchived_tool_message(message: ChatMessage) -> bool:
    if message.role != "tool":
        return False
    parsed = _parse_json_object(message.content)
    return not bool(parsed and parsed.get("archived") is True)


def _tail_split_index(messages: list[ChatMessage], target_tokens: int, min_messages: int) -> int:
    if len(messages) <= min_messages:
        return 0
    total = 0
    kept = 0
    split_index = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        if kept >= min_messages and total >= target_tokens:
            break
        total += estimate_message_tokens(messages[index])
        kept += 1
        split_index = index
    while split_index > 0 and messages[split_index].role == "tool":
        split_index -= 1
    return split_index


def _summary_prompt(messages: list[ChatMessage]) -> str:
    serialized = _json_dumps([_message_payload(message) for message in messages])
    return "\n".join(
        [
            "请把下面较早的 MonkeyCode 对话历史压缩成正式摘要。",
            "绝对禁止调用任何工具。",
            "请先在内部写分析草稿，识别目标、事实、决策、文件路径、归档索引和风险；草稿用完即丢弃，不要输出。",
            "正式摘要必须使用以下固定部分：",
            "1. 当前目标",
            "2. 已完成事实",
            "3. 关键决策",
            "4. 未完成事项",
            "5. 重要文件/路径",
            "6. 工具结果归档索引",
            "7. 风险",
            "8. 下一步建议",
            "不要把摘要当作代码事实来源；缺少细节时写明需要重新读取源文件或归档。",
            "",
            "较早历史 JSON：",
            serialized,
        ]
    )


def _stream_provider_without_tools(
    provider: Any,
    messages: list[ChatMessage],
    prompt_payload: ProviderPromptPayload,
):
    signature = inspect.signature(provider.stream_chat)
    parameters = signature.parameters
    accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    accepts_tools = "tools" in parameters or accepts_kwargs or len(positional) >= 2
    args: list[Any] = [messages]
    if accepts_tools:
        args.append(None)
    kwargs: dict[str, Any] = {}
    if "allow_tool_calls" in parameters or accepts_kwargs:
        kwargs["allow_tool_calls"] = False
    if "prompt_payload" in parameters or accepts_kwargs:
        kwargs["prompt_payload"] = prompt_payload
    return provider.stream_chat(*args, **kwargs)


def _usage_token_count(usage: dict[str, Any] | None) -> int | None:
    if not isinstance(usage, dict):
        return None
    for key in ("prompt_tokens", "input_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_calls": [asdict(call) for call in message.tool_calls or []],
        "tool_call_id": message.tool_call_id,
        "provider_payload": message.provider_payload,
    }


def _parse_json_object(content: str | list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(content, str):
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tool_name_from_result(parsed: dict[str, Any]) -> str | None:
    value = parsed.get("tool_name")
    return value if isinstance(value, str) else None


def _preview_text(content: str | list[dict[str, Any]], *, limit: int = 1000) -> str:
    text = content if isinstance(content, str) else _json_dumps(content)
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
