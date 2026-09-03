from monkeycode.config import AppConfig, SecretValue
from monkeycode.errors import UnsupportedProtocolError
from monkeycode.providers.anthropic import AnthropicProvider
from monkeycode.providers.factory import create_provider
from monkeycode.providers.openai import OpenAIProvider


def make_config(protocol: str) -> AppConfig:
    return AppConfig(
        protocol=protocol,
        model="test-model",
        base_url="https://example.test",
        api_key=SecretValue("sk-test"),
        options={},
    )


def test_creates_openai_provider() -> None:
    assert isinstance(create_provider(make_config("openai")), OpenAIProvider)


def test_creates_anthropic_provider() -> None:
    assert isinstance(create_provider(make_config("anthropic")), AnthropicProvider)


def test_rejects_unknown_provider() -> None:
    try:
        create_provider(make_config("unknown"))
    except UnsupportedProtocolError as error:
        assert "anthropic" in str(error)
        assert "openai" in str(error)
    else:
        raise AssertionError("expected UnsupportedProtocolError")
