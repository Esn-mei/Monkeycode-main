from __future__ import annotations

import logging
from typing import Any

SENSITIVE_PARTS = ("authorization", "api_key", "apikey", "token", "password", "secret")
REDACTED = "***REDACTED***"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value


def log_hook_event(
    logger: logging.Logger,
    *,
    level: int = logging.INFO,
    event: str,
    rule_id: str | None = None,
    action_type: str | None = None,
    status: str,
    reason: str | None = None,
    duration_ms: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        safe_details = redact(details or {})
        parts = [
            "hook",
            f"event={event}",
            f"status={status}",
        ]
        if rule_id:
            parts.append(f"rule={rule_id}")
        if action_type:
            parts.append(f"action={action_type}")
        if duration_ms is not None:
            parts.append(f"duration_ms={duration_ms}")
        if reason:
            parts.append(f"reason={reason}")
        if safe_details:
            parts.append(f"details={safe_details}")
        logger.log(level, " ".join(parts))
    except Exception:
        return


def get_hook_logger() -> logging.Logger:
    return logging.getLogger("monkeycode.hooks")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SENSITIVE_PARTS)
