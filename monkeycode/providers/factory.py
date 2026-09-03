from monkeycode.config import AppConfig
from monkeycode.errors import UnsupportedProtocolError
from monkeycode.providers.anthropic import AnthropicProvider
from monkeycode.providers.openai import OpenAIProvider
from monkeycode.providers.base import ChatProvider


def create_provider(config: AppConfig) -> ChatProvider:
    if config.protocol == "openai":
        return OpenAIProvider(config)
    if config.protocol == "anthropic":
        return AnthropicProvider(config)
    raise UnsupportedProtocolError(
        f"unsupported protocol {config.protocol!r}; supported values: anthropic, openai"
    )
