from monkeycode.agent_fork import build_forked_messages, is_fork_context
from monkeycode.messages import ChatMessage, ToolCall


def test_build_forked_messages_empty_parent() -> None:
    messages = build_forked_messages([], "do it")

    assert len(messages) == 1
    assert messages[0].role == "user"
    assert "do it" in messages[0].content


def test_build_forked_messages_adds_missing_tool_results() -> None:
    parent = [
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_1", name="read_file", arguments_json="{}")],
        )
    ]

    messages = build_forked_messages(parent, "next")

    assert messages[-2].role == "tool"
    assert messages[-2].tool_call_id == "call_1"
    assert is_fork_context(messages) is True


def test_is_fork_context_false() -> None:
    assert is_fork_context([ChatMessage(role="user", content="hello")]) is False
