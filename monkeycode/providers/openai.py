from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import httpx

from monkeycode.config import AppConfig
from monkeycode.cache_usage import parse_cache_usage
from monkeycode.errors import StreamParseError
from monkeycode.messages import ChatMessage, StreamEvent, ToolCall, ToolDefinition
from monkeycode.prompting import ProviderPromptPayload, enhance_tool_definitions
from monkeycode.prompts import SYSTEM_PROMPT
from monkeycode.providers.base import HTTPProviderBase
from monkeycode.sse import iter_sse_events


class OpenAIProvider(HTTPProviderBase):
    def __init__(self, config: AppConfig, client: httpx.Client | None = None) -> None:
        super().__init__(config, client)

    def stream_chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        allow_tool_calls: bool = True,
        prompt_payload: ProviderPromptPayload | None = None,
    ) -> Iterable[StreamEvent]:
        prompt_messages = _prompt_messages(prompt_payload)
        payload = {
            "model": self.config.model,
            "messages": [
                *prompt_messages,
                *[_message_to_openai(message) for message in messages],
            ],
            "stream": True,
        }
        if tools:
            payload["tools"] = [_tool_to_openai(tool) for tool in enhance_tool_definitions(tools)]
            payload["tool_choice"] = "auto" if allow_tool_calls else "none"
            if allow_tool_calls:
                payload["parallel_tool_calls"] = True
        payload.update(self.config.options)
        payload.pop("thinking", None)

        headers = {
            "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

        try:
            with self.client.stream(
                "POST",
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                self._handle_status(response)
                tool_call_parts: dict[int, dict[str, str]] = {}
                for event in iter_sse_events(response.iter_lines()):
                    if event.data == "[DONE]":
                        for tool_call in _finish_tool_calls(tool_call_parts):
                            yield StreamEvent(type="tool_call", tool_call=tool_call)
                        yield StreamEvent(type="done")
                        return
                    try:
                        chunk = json.loads(event.data)
                    except json.JSONDecodeError as exc:
                        raise StreamParseError("invalid OpenAI stream JSON") from exc
                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        yield StreamEvent(
                            type="usage",
                            usage=usage,
                            cache_usage=parse_cache_usage("openai", usage).to_dict(),
                        )
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta", {})
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            yield StreamEvent(type="reasoning_delta", text=reasoning)
                        text = delta.get("content")
                        if text:
                            yield StreamEvent(type="text_delta", text=text)
                        for tool_delta in delta.get("tool_calls", []) or []:
                            index = int(tool_delta.get("index", 0))
                            part = tool_call_parts.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if tool_delta.get("id"):
                                part["id"] += tool_delta["id"]
                            function = tool_delta.get("function") or {}
                            if function.get("name"):
                                part["name"] += function["name"]
                            if function.get("arguments"):
                                part["arguments"] += function["arguments"]
        except httpx.HTTPError as exc:
            raise self._map_network_error(exc) from exc

        yield StreamEvent(type="done")


def _prompt_messages(prompt_payload: ProviderPromptPayload | None) -> list[dict[str, str]]:
    if prompt_payload is None:
        return [{"role": "system", "content": SYSTEM_PROMPT}]
    messages = [{"role": "system", "content": prompt_payload.stable_system_text}]
    messages.extend(
        {"role": "system", "content": message}
        for message in prompt_payload.dynamic_system_messages
        if message.strip()
    )
    return messages


def _tool_to_openai(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        },
    }


def _message_to_openai(message: ChatMessage) -> dict[str, Any]:
    if message.role == "assistant" and message.tool_calls:
        payload = {
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments_json},
                }
                for call in message.tool_calls
            ],
        }
        if message.provider_payload:
            reasoning_content = message.provider_payload.get("reasoning_content")
            if reasoning_content:
                payload["reasoning_content"] = reasoning_content
        return payload
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    return {"role": message.role, "content": message.content}


def _finish_tool_calls(parts: dict[int, dict[str, str]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index in sorted(parts):
        part = parts[index]
        arguments_json = part["arguments"]
        calls.append(
            ToolCall(
                id=part["id"] or f"tool_call_{index}",
                name=part["name"],
                arguments_json=arguments_json,
                arguments=_parse_arguments(arguments_json),
                provider="openai",
            )
        )
    return calls


def _parse_arguments(arguments_json: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
