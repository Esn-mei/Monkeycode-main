from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import httpx

from monkeycode.cache_usage import parse_cache_usage
from monkeycode.config import AppConfig
from monkeycode.errors import StreamParseError
from monkeycode.messages import ChatMessage, StreamEvent, ToolCall, ToolDefinition
from monkeycode.prompting import ProviderPromptPayload, enhance_tool_definitions
from monkeycode.prompts import SYSTEM_PROMPT
from monkeycode.providers.base import HTTPProviderBase
from monkeycode.sse import iter_sse_events


class AnthropicProvider(HTTPProviderBase):
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
        options = dict(self.config.options)
        thinking = options.pop("thinking", None)
        payload = {
            "model": self.config.model,
            "messages": [_message_to_anthropic(message) for message in messages],
            "stream": True,
            "max_tokens": options.pop("max_tokens", 1024),
            "system": _system_to_anthropic(prompt_payload),
        }
        if tools:
            payload["tools"] = [_tool_to_anthropic(tool) for tool in enhance_tool_definitions(tools)]
            if not allow_tool_calls:
                payload["tool_choice"] = {"type": "none"}
        payload.update(options)
        if thinking:
            payload["thinking"] = thinking

        headers = {
            "x-api-key": self.config.api_key.get_secret_value(),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        try:
            with self.client.stream(
                "POST",
                f"{self.config.base_url}/v1/messages",
                headers=headers,
                json=payload,
            ) as response:
                self._handle_status(response)
                tool_blocks: dict[int, dict[str, str]] = {}
                for event in iter_sse_events(response.iter_lines()):
                    if event.event == "message_stop":
                        for tool_call in _finish_tool_calls(tool_blocks):
                            yield StreamEvent(type="tool_call", tool_call=tool_call)
                        yield StreamEvent(type="done")
                        return
                    try:
                        chunk = json.loads(event.data)
                    except json.JSONDecodeError as exc:
                        raise StreamParseError("invalid Anthropic stream JSON") from exc
                    if chunk.get("type") == "content_block_start":
                        index = int(chunk.get("index", 0))
                        block = chunk.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            tool_blocks[index] = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "arguments": "",
                            }
                    delta = chunk.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield StreamEvent(type="text_delta", text=delta["text"])
                    if delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        yield StreamEvent(type="reasoning_delta", text=delta["thinking"])
                    if delta.get("type") == "input_json_delta":
                        index = int(chunk.get("index", 0))
                        part = tool_blocks.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        part["arguments"] += delta.get("partial_json", "")
                    usage = chunk.get("usage") or delta.get("usage")
                    if isinstance(usage, dict):
                        yield StreamEvent(
                            type="usage",
                            usage=usage,
                            cache_usage=parse_cache_usage("anthropic", usage).to_dict(),
                        )
        except httpx.HTTPError as exc:
            raise self._map_network_error(exc) from exc

        yield StreamEvent(type="done")


def _system_to_anthropic(prompt_payload: ProviderPromptPayload | None) -> str | list[dict[str, Any]]:
    if prompt_payload is None:
        return SYSTEM_PROMPT
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": prompt_payload.stable_system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    blocks.extend(
        {"type": "text", "text": message}
        for message in prompt_payload.dynamic_system_messages
        if message.strip()
    )
    return blocks


def _tool_to_anthropic(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters_schema,
    }


def _message_to_anthropic(message: ChatMessage) -> dict[str, Any]:
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments if call.arguments is not None else _parse_arguments(call.arguments_json),
                }
                for call in message.tool_calls
            ],
        }
    if message.role == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": str(message.content),
                }
            ],
        }
    return {"role": message.role, "content": message.content}


def _finish_tool_calls(parts: dict[int, dict[str, str]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index in sorted(parts):
        part = parts[index]
        arguments_json = part["arguments"]
        calls.append(
            ToolCall(
                id=part["id"] or f"tool_use_{index}",
                name=part["name"],
                arguments_json=arguments_json,
                arguments=_parse_arguments(arguments_json),
                provider="anthropic",
            )
        )
    return calls


def _parse_arguments(arguments_json: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
