from typing import Any

from monkeycode.messages import ChatMessage, ToolCall


def _sanitize_message_content(content: str) -> str:
    return content.encode("utf-8", errors="replace").decode("utf-8")


class ChatSession:
    def __init__(self) -> None:
        self._messages: list[ChatMessage] = []

    @property
    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    def replace_messages(self, messages: list[ChatMessage]) -> None:
        self._messages = list(messages)

    def add_user_message(self, content: str) -> None:
        self._messages.append(ChatMessage(role="user", content=_sanitize_message_content(content)))

    def add_assistant_message(self, content: str) -> None:
        self._messages.append(ChatMessage(role="assistant", content=_sanitize_message_content(content)))

    def add_assistant_tool_call(
        self,
        tool_call: ToolCall,
        *,
        content: str = "",
        provider_payload: dict[str, Any] | None = None,
    ) -> None:
        self.add_assistant_tool_calls([tool_call], content=content, provider_payload=provider_payload)

    def add_assistant_tool_calls(
        self,
        tool_calls: list[ToolCall],
        *,
        content: str = "",
        provider_payload: dict[str, Any] | None = None,
    ) -> None:
        self._messages.append(
            ChatMessage(
                role="assistant",
                content=_sanitize_message_content(content),
                tool_calls=list(tool_calls),
                provider_payload=provider_payload,
            )
        )

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self._messages.append(
            ChatMessage(
                role="tool",
                content=_sanitize_message_content(content),
                tool_call_id=tool_call_id,
            )
        )
