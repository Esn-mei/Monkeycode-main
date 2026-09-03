import pytest

from monkeycode.errors import StreamParseError
from monkeycode.sse import SSEEvent, iter_sse_events


def test_parses_event_and_data() -> None:
    lines = ["event: message\n", 'data: {"text":"hi"}\n', "\n"]

    assert list(iter_sse_events(lines)) == [
        SSEEvent(event="message", data='{"text":"hi"}')
    ]


def test_merges_multiple_data_lines() -> None:
    lines = ["data: line 1\n", "data: line 2\n", "\n"]

    assert list(iter_sse_events(lines)) == [SSEEvent(event=None, data="line 1\nline 2")]


def test_ignores_comments_and_empty_events() -> None:
    lines = [": ping\n", "\n", "data: value\n", "\n"]

    assert list(iter_sse_events(lines)) == [SSEEvent(event=None, data="value")]


def test_preserves_done_sentinel() -> None:
    assert list(iter_sse_events(["data: [DONE]\n", "\n"])) == [
        SSEEvent(event=None, data="[DONE]")
    ]


def test_rejects_malformed_line() -> None:
    with pytest.raises(StreamParseError):
        list(iter_sse_events(["not valid\n", "\n"]))
