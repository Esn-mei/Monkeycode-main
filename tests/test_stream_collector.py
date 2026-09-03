import pytest

from monkeycode.messages import StreamEvent, ToolCall
from monkeycode.streaming import StreamCollector


def test_collects_text_while_emitting_deltas() -> None:
    collector = StreamCollector()

    events = list(
        collector.collect(
            [
                StreamEvent(type="text_delta", text="Hel"),
                StreamEvent(type="text_delta", text="lo"),
                StreamEvent(type="done"),
            ],
            iteration=1,
        )
    )

    assert [event.text for event in events if event.type == "text_delta"] == ["Hel", "lo"]
    assert collector.final_response.assistant_text == "Hello"


def test_collects_multiple_tool_calls_reasoning_and_usage() -> None:
    collector = StreamCollector()
    call_1 = ToolCall(id="call_1", name="read_file", arguments_json="{}")
    call_2 = ToolCall(id="call_2", name="find_files", arguments_json="{}")

    events = list(
        collector.collect(
            [
                StreamEvent(type="reasoning_delta", text="think"),
                StreamEvent(type="tool_call", tool_call=call_1),
                StreamEvent(type="tool_call", tool_call=call_2),
                StreamEvent(
                    type="usage",
                    usage={"total_tokens": 12},
                    cache_usage={"provider": "openai", "available": True, "cached_tokens": 8},
                ),
                StreamEvent(type="done"),
            ],
            iteration=1,
        )
    )

    assert collector.final_response.reasoning_text == "think"
    assert collector.final_response.tool_calls == [call_1, call_2]
    assert collector.final_response.usage == {"total_tokens": 12}
    assert collector.final_response.cache_usage == {
        "provider": "openai",
        "available": True,
        "cached_tokens": 8,
    }
    assert [event.usage for event in events if event.type == "usage"] == [{"total_tokens": 12}]
    assert [event.metadata["cache_usage"] for event in events if event.type == "usage"] == [
        {"provider": "openai", "available": True, "cached_tokens": 8}
    ]


def test_can_buffer_text_without_emitting_deltas() -> None:
    collector = StreamCollector()

    events = list(
        collector.collect(
            [StreamEvent(type="text_delta", text="hidden"), StreamEvent(type="done")],
            iteration=1,
            emit_text_events=False,
        )
    )

    assert events == []
    assert collector.final_response.assistant_text == "hidden"


def test_stream_exception_is_left_for_agent_runner() -> None:
    collector = StreamCollector()

    def broken_events():
        yield StreamEvent(type="text_delta", text="before")
        raise RuntimeError("bad stream")

    with pytest.raises(RuntimeError, match="bad stream"):
        list(collector.collect(broken_events(), iteration=1))
