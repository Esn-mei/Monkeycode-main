import httpx
import pytest

from monkeycode.config import AppConfig, SecretValue
from monkeycode.errors import ApiStatusError, AuthenticationError, NetworkError
from monkeycode.messages import ChatMessage, ToolCall, ToolDefinition
from monkeycode.prompting import ProviderPromptPayload
from monkeycode.prompts import SYSTEM_PROMPT
from monkeycode.providers.anthropic import AnthropicProvider


def config() -> AppConfig:
    return AppConfig(
        protocol="anthropic",
        model="claude-test",
        base_url="https://api.anthropic.test",
        api_key=SecretValue("sk-ant-secret"),
        options={
            "max_tokens": 512,
            "thinking": {"type": "adaptive", "effort": "medium"},
        },
    )


def test_streams_text_delta_and_transmits_thinking_config() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers["x-api-key"]
        captured["json"] = dict(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'event: content_block_delta\n'
                'data: {"delta":{"type":"text_delta","text":"Hi"}}\n\n'
                'event: content_block_delta\n'
                'data: {"delta":{"type":"thinking_delta","thinking":"hidden"}}\n\n'
                'event: message_stop\n'
                "data: {}\n\n"
            ),
        )

    provider = AnthropicProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))

    events = list(provider.stream_chat([ChatMessage(role="user", content="hi")]))

    assert [event.text for event in events if event.type == "text_delta"] == ["Hi"]
    assert [event.text for event in events if event.type == "reasoning_delta"] == ["hidden"]
    assert captured["url"] == "https://api.anthropic.test/v1/messages"
    assert captured["api_key"] == "sk-ant-secret"
    assert captured["json"]["stream"] is True
    assert captured["json"]["model"] == "claude-test"
    assert captured["json"]["system"] == SYSTEM_PROMPT
    assert captured["json"]["max_tokens"] == 512
    assert captured["json"]["thinking"] == {"type": "adaptive", "effort": "medium"}


def test_streams_tool_use_partial_json_and_sends_tools() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = dict(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'event: content_block_start\n'
                'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"read_file","input":{}}}\n\n'
                'event: content_block_delta\n'
                'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"pa"}}\n\n'
                'event: content_block_delta\n'
                'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"th\\":\\"README.md\\"}"}}\n\n'
                'event: message_stop\n'
                'data: {}\n\n'
            ),
        )

    provider = AnthropicProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    tools = [
        ToolDefinition(
            name="read_file",
            description="Read file.",
            parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
    ]

    events = list(provider.stream_chat([ChatMessage(role="user", content="hi")], tools))

    tool_call = next(event.tool_call for event in events if event.type == "tool_call")
    assert captured["json"]["tools"][0]["name"] == "read_file"
    assert captured["json"]["thinking"] == {"type": "adaptive", "effort": "medium"}
    assert tool_call.id == "toolu_1"
    assert tool_call.name == "read_file"
    assert tool_call.arguments == {"path": "README.md"}


def test_streams_multiple_tool_use_blocks_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'event: content_block_start\n'
                'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"read_file","input":{}}}\n\n'
                'event: content_block_delta\n'
                'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{}"}}\n\n'
                'event: content_block_start\n'
                'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_2","name":"find_files","input":{}}}\n\n'
                'event: content_block_delta\n'
                'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"pattern\\":\\"*.py\\"}"}}\n\n'
                'event: message_delta\n'
                'data: {"type":"message_delta","usage":{"output_tokens":12}}\n\n'
                'event: message_stop\n'
                'data: {}\n\n'
            ),
        )

    provider = AnthropicProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    tools = [
        ToolDefinition(name="read_file", description="Read.", parameters_schema={"type": "object"}),
        ToolDefinition(name="find_files", description="Find.", parameters_schema={"type": "object"}),
    ]

    events = list(provider.stream_chat([ChatMessage(role="user", content="hi")], tools))

    tool_calls = [event.tool_call for event in events if event.type == "tool_call"]
    assert [call.name for call in tool_calls] == ["read_file", "find_files"]
    assert tool_calls[1].arguments == {"pattern": "*.py"}
    assert [event.usage for event in events if event.type == "usage"] == [{"output_tokens": 12}]


def test_sends_cacheable_structured_system_blocks_and_cache_usage() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = dict(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'event: message_delta\n'
                'data: {"type":"message_delta","usage":{"cache_creation_input_tokens":10,"cache_read_input_tokens":20}}\n\n'
                'event: message_stop\n'
                "data: {}\n\n"
            ),
        )

    provider = AnthropicProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    prompt_payload = ProviderPromptPayload(
        stable_system_text="stable prompt",
        dynamic_system_messages=["dynamic context"],
    )

    events = list(
        provider.stream_chat(
            [ChatMessage(role="user", content="hi")],
            prompt_payload=prompt_payload,
        )
    )

    assert captured["json"]["system"][0] == {
        "type": "text",
        "text": "stable prompt",
        "cache_control": {"type": "ephemeral"},
    }
    assert captured["json"]["system"][1] == {"type": "text", "text": "dynamic context"}
    usage_event = next(event for event in events if event.type == "usage")
    assert usage_event.cache_usage["available"] is True
    assert usage_event.cache_usage["cache_creation_tokens"] == 10
    assert usage_event.cache_usage["cache_read_tokens"] == 20


def test_sends_tool_result_history() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = dict(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="event: message_stop\ndata: {}\n\n",
        )

    provider = AnthropicProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    tool_call = ToolCall(
        id="toolu_1",
        name="read_file",
        arguments_json='{"path":"README.md"}',
        arguments={"path": "README.md"},
    )

    list(
        provider.stream_chat(
            [
                ChatMessage(role="assistant", content="", tool_calls=[tool_call]),
                ChatMessage(role="tool", content='{"success":true}', tool_call_id="toolu_1"),
            ]
        )
    )

    assert captured["json"]["messages"][0]["content"][0]["type"] == "tool_use"
    assert captured["json"]["messages"][1]["role"] == "user"
    assert captured["json"]["messages"][1]["content"][0]["tool_use_id"] == "toolu_1"


def test_maps_authentication_errors_without_leaking_key() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, text="bad key"))
    )
    provider = AnthropicProvider(config(), client=client)

    with pytest.raises(AuthenticationError) as error:
        list(provider.stream_chat([ChatMessage(role="user", content="hi")]))

    assert "sk-ant-secret" not in str(error.value)


def test_maps_server_errors() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    )
    provider = AnthropicProvider(config(), client=client)

    with pytest.raises(ApiStatusError, match="500"):
        list(provider.stream_chat([ChatMessage(role="user", content="hi")]))


def test_maps_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    provider = AnthropicProvider(config(), client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(NetworkError):
        list(provider.stream_chat([ChatMessage(role="user", content="hi")]))
