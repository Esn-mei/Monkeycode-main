from __future__ import annotations

from collections.abc import Iterable
import json
from typing import TYPE_CHECKING, Protocol

import httpx

from monkeycode.config import AppConfig
from monkeycode.errors import ApiStatusError, AuthenticationError, NetworkError
from monkeycode.messages import ChatMessage, StreamEvent, ToolDefinition

if TYPE_CHECKING:
    from monkeycode.prompting import ProviderPromptPayload


class ChatProvider(Protocol):
    def stream_chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        allow_tool_calls: bool = True,
        prompt_payload: ProviderPromptPayload | None = None,
    ) -> Iterable[StreamEvent]:
        ...


class HTTPProviderBase:
    def __init__(self, config: AppConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=None)

    def _handle_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = _response_error_detail(response)
        if response.status_code in {401, 403}:
            message = f"authentication failed with status {response.status_code}"
            if detail:
                message = f"{message}: {detail}"
            raise AuthenticationError(message)
        message = f"API request failed with status {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        raise ApiStatusError(message)

    def _map_network_error(self, exc: httpx.HTTPError) -> NetworkError:
        return NetworkError(f"network error: {exc.__class__.__name__}")


def _response_error_detail(response: httpx.Response) -> str | None:
    try:
        response.read()
    except httpx.HTTPError:
        return None
    text = response.text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text[:500]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"][:500]
    return text[:500]
