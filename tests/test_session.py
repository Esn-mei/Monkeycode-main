from monkeycode.messages import ChatMessage, ToolCall
from monkeycode.session import ChatSession


def test_records_user_and_successful_assistant_messages_in_order() -> None:
    session = ChatSession()

    session.add_user_message("hello")
    session.add_assistant_message("hi")
    session.add_user_message("remember me")

    assert session.messages == [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi"),
        ChatMessage(role="user", content="remember me"),
    ]


def test_failed_assistant_response_is_not_added() -> None:
    session = ChatSession()

    session.add_user_message("hello")

    assert session.messages == [ChatMessage(role="user", content="hello")]


def test_messages_returns_copy() -> None:
    session = ChatSession()
    session.add_user_message("hello")

    messages = session.messages
    messages.append(ChatMessage(role="assistant", content="mutated"))

    assert session.messages == [ChatMessage(role="user", content="hello")]


def test_user_message_replaces_invalid_surrogate_characters() -> None:
    session = ChatSession()

    session.add_user_message("hello \udc80")

    assert session.messages == [ChatMessage(role="user", content="hello ?")]


def test_records_tool_call_and_tool_result() -> None:
    session = ChatSession()
    tool_call = ToolCall(
        id="call_1",
        name="read_file",
        arguments_json='{"path":"README.md"}',
        arguments={"path": "README.md"},
        provider="test",
    )

    session.add_assistant_tool_call(tool_call)
    session.add_tool_result("call_1", '{"success":true}')

    assert session.messages[0].tool_calls == [tool_call]
    assert session.messages[1].role == "tool"
    assert session.messages[1].tool_call_id == "call_1"
