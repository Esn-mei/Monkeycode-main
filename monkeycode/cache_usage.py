from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CacheUsage:
    provider: str
    available: bool = False
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cached_tokens: int | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "available": self.available,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cached_tokens": self.cached_tokens,
            "raw_usage": self.raw_usage,
        }


def parse_cache_usage(provider: str, usage: dict[str, Any] | None) -> CacheUsage:
    raw_usage = dict(usage or {})
    if not usage:
        return CacheUsage(provider=provider, raw_usage=raw_usage)

    normalized_provider = provider.lower()
    if normalized_provider == "anthropic":
        creation = _find_int(usage, "cache_creation_input_tokens")
        read = _find_int(usage, "cache_read_input_tokens")
        available = creation is not None or read is not None
        return CacheUsage(
            provider=provider,
            available=available,
            cache_read_tokens=read,
            cache_creation_tokens=creation,
            cached_tokens=read,
            raw_usage=raw_usage,
        )

    cached = (
        _nested_int(usage, ("prompt_tokens_details", "cached_tokens"))
        or _nested_int(usage, ("input_tokens_details", "cached_tokens"))
        or _find_int(usage, "cached_tokens")
    )
    return CacheUsage(
        provider=provider,
        available=cached is not None,
        cached_tokens=cached,
        cache_read_tokens=cached,
        raw_usage=raw_usage,
    )


def _nested_int(data: dict[str, Any], path: tuple[str, ...]) -> int | None:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, int) else None


def _find_int(value: Any, key: str) -> int | None:
    if isinstance(value, dict):
        current = value.get(key)
        if isinstance(current, int):
            return current
        for child in value.values():
            found = _find_int(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_int(child, key)
            if found is not None:
                return found
    return None
