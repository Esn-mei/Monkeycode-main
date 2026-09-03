class MonkeyCodeError(Exception):
    """Base error for user-facing MonkeyCode failures."""


class ConfigError(MonkeyCodeError):
    pass


class UnsupportedProtocolError(ConfigError):
    pass


class ProviderError(MonkeyCodeError):
    pass


class AuthenticationError(ProviderError):
    pass


class NetworkError(ProviderError):
    pass


class ApiStatusError(ProviderError):
    pass


class StreamParseError(ProviderError):
    pass
