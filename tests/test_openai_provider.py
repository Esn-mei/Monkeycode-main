import httpx
import pytest

from monkeycode.config import AppConfig, SecretValue
from monkeycode.errors import ApiStatusError, AuthenticationError, NetworkError
from monkeycode.messages import ChatMessage, ToolCall, ToolDefinition
from monkeycode.prompting import ProviderPromptPayload
from monkeycode.prompts import SYSTEM_PROMPT
from monkeycode.providers.openai import OpenAIProvider


def config() -> AppConfig:
    return AppConfig(
        protocol="openai",
        model="gpt-test",
        base_url="https://api.example.test/v1",
        api_key=SecretValue("sk-secret-openai"),
        options={"temperature": 0.1},
    )


def test_streams_text_deltas_and_sends_chat_completion_request() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["json"] = dict(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(config(), client=client)

    events = list(provider.stream_chat([ChatMessage(role="user", content="hi")]))

    assert [event.text for event in events if event.type == "text_delta"] == ["Hel", "lo"]
    assert captured["url"] == "https://api.example.test/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-secret-openai"
    assert captured["json"]["stream"] is True
    assert captured["json"]["model"] == "gpt-test"
    assert captured["json"]["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "hi"},
    ]
    assert captured["json"]["temperature"] == 0.1


def test_streams_tool_call_deltas_and_sends_tool_definitions() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = dict(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"reasoning_content":"Need files."}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_file","arguments":"{\\"pa"}}]}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\\":\\"README.md\\"}"}}]}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    provider = OpenAIProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    tools = [
        ToolDefinition(
            name="read_file",
            description="Read file.",
            parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
    ]

    events = list(provider.stream_chat([ChatMessage(role="user", content="hi")], tools))

    assert [event.text for event in events if event.type == "reasoning_delta"] == ["Need files."]
    tool_call = next(event.tool_call for event in events if event.type == "tool_call")
    assert captured["json"]["tools"][0]["function"]["name"] == "read_file"
    assert captured["json"]["tool_choice"] == "auto"
    assert captured["json"]["parallel_tool_calls"] is True
    assert tool_call.id == "call_1"
    assert tool_call.name == "read_file"
    assert tool_call.arguments == {"path": "README.md"}


def test_streams_multiple_tool_calls_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read_file","arguments":"{}"}},{"index":1,"id":"call_2","function":{"name":"find_files","arguments":"{\\"pattern\\":\\"*.py\\"}"}}]}}]}\n\n'
                'data: {"choices":[],"usage":{"total_tokens":42}}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    provider = OpenAIProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    tools = [
        ToolDefinition(name="read_file", description="Read.", parameters_schema={"type": "object"}),
        ToolDefinition(name="find_files", description="Find.", parameters_schema={"type": "object"}),
    ]

    events = list(provider.stream_chat([ChatMessage(role="user", content="hi")], tools))

    tool_calls = [event.tool_call for event in events if event.type == "tool_call"]
    assert [call.name for call in tool_calls] == ["read_file", "find_files"]
    assert tool_calls[1].arguments == {"pattern": "*.py"}
    assert [event.usage for event in events if event.type == "usage"] == [{"total_tokens": 42}]


def test_sends_structured_prompt_payload_and_cache_usage() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = dict(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"ok"}}],"usage":{"prompt_tokens_details":{"cached_tokens":64}}}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    provider = OpenAIProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    prompt_payload = ProviderPromptPayload(
        stable_system_text="stable prompt",
        dynamic_system_messages=["dynamic context", "dynamic mode"],
    )

    events = list(
        provider.stream_chat(
            [ChatMessage(role="user", content="hi")],
            prompt_payload=prompt_payload,
        )
    )

    assert captured["json"]["messages"][:3] == [
        {"role": "system", "content": "stable prompt"},
        {"role": "system", "content": "dynamic context"},
        {"role": "system", "content": "dynamic mode"},
    ]
    usage_event = next(event for event in events if event.type == "usage")
    assert usage_event.cache_usage["available"] is True
    assert usage_event.cache_usage["cached_tokens"] == 64


def test_can_send_tools_while_disallowing_more_tool_calls() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = dict(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content='data: {"choices":[{"delta":{"content":"Done"}}]}\n\ndata: [DONE]\n\n',
        )

    provider = OpenAIProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    tools = [
        ToolDefinition(
            name="find_files",
            description="Find files.",
            parameters_schema={"type": "object", "properties": {"pattern": {"type": "string"}}},
        )
    ]

    events = list(
        provider.stream_chat(
            [ChatMessage(role="user", content="summarize")],
            tools,
            allow_tool_calls=False,
        )
    )

    assert captured["json"]["tools"][0]["function"]["name"] == "find_files"
    assert captured["json"]["tool_choice"] == "none"
    assert "parallel_tool_calls" not in captured["json"]
    assert [event.text for event in events if event.type == "text_delta"] == ["Done"]


def test_sends_tool_result_history() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = dict(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="data: [DONE]\n\n",
        )

    provider = OpenAIProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    tool_call = ToolCall(id="call_1", name="read_file", arguments_json='{"path":"README.md"}')

    list(
        provider.stream_chat(
            [
                ChatMessage(
                    role="assistant",
                    content="Let me check.",
                    tool_calls=[tool_call],
                    provider_payload={"reasoning_content": "Need to read the file."},
                ),
                ChatMessage(role="tool", content='{"success":true}', tool_call_id="call_1"),
            ]
        )
    )

    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["messages"][1]["tool_calls"][0]["id"] == "call_1"
    assert captured["json"]["messages"][1]["content"] == "Let me check."
    assert captured["json"]["messages"][1]["reasoning_content"] == "Need to read the file."
    assert captured["json"]["messages"][2]["role"] == "tool"
    assert captured["json"]["messages"][2]["tool_call_id"] == "call_1"


def test_maps_authentication_errors_without_leaking_key() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="bad key"))
    )
    provider = OpenAIProvider(config(), client=client)

    with pytest.raises(AuthenticationError) as error:
        list(provider.stream_chat([ChatMessage(role="user", content="hi")]))

    assert "sk-secret-openai" not in str(error.value)


def test_maps_server_errors() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                500,
                json={"error": {"message": "provider exploded"}},
            )
        )
    )
    provider = OpenAIProvider(config(), client=client)

    with pytest.raises(ApiStatusError, match="provider exploded"):
        list(provider.stream_chat([ChatMessage(role="user", content="hi")]))


def test_maps_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    provider = OpenAIProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(NetworkError):
        list(provider.stream_chat([ChatMessage(role="user", content="hi")]))
