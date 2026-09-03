from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from monkeycode.events import AgentEvent
from monkeycode.messages import StreamEvent, ToolCall


@dataclass(frozen=True)
class CollectedResponse:
    assistant_text: str = ""
    reasoning_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    cache_usage: dict[str, Any] | None = None
    raw_provider_metadata: dict[str, Any] = field(default_factory=dict)


class StreamCollector:
    def __init__(self) -> None:
        self.final_response = CollectedResponse()

    def collect(
        self,
        provider_events: Iterable[StreamEvent],
        *,
        iteration: int,
        turn_index: int = 0,
        emit_text_events: bool = True,
    ) -> Iterator[AgentEvent]:
        assistant_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage: dict[str, Any] | None = None
        cache_usage: dict[str, Any] | None = None

        for event in provider_events:
            if event.type == "text_delta" and event.text:
                assistant_parts.append(event.text)
                if emit_text_events:
                    yield AgentEvent(
                        type="text_delta",
                        turn_index=turn_index,
                        iteration=iteration,
                        text=event.text,
                    )
            elif event.type == "reasoning_delta" and event.text:
                reasoning_parts.append(event.text)
            elif event.type == "tool_call" and event.tool_call:
                tool_calls.append(event.tool_call)
            elif event.type == "usage" and event.usage is not None:
                usage = event.usage
                cache_usage = event.cache_usage
                yield AgentEvent(
                    type="usage",
                    turn_index=turn_index,
                    iteration=iteration,
                    usage=usage,
                    metadata={"cache_usage": cache_usage} if cache_usage else {},
                )
            elif event.type == "error":
                yield AgentEvent(
                    type="error",
                    turn_index=turn_index,
                    iteration=iteration,
                    error_type=event.error_type or "stream_error",
                    error_message=event.error_message or "provider stream error",
                )

        self.final_response = CollectedResponse(
            assistant_text="".join(assistant_parts),
            reasoning_text="".join(reasoning_parts),
            tool_calls=tool_calls,
            usage=usage,
            cache_usage=cache_usage,
        )
