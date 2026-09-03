from __future__ import annotations

from monkeycode.hooks.types import (
    HookActionResult,
    HookActionSpec,
    HookConfig,
    HookCondition,
    HookDispatchResult,
    HookEventContext,
    HookExecutionControl,
    HookMatchClause,
    HookRule,
    HookToolDecision,
    ValidationIssue,
)

__all__ = [
    "HookActionResult",
    "HookActionSpec",
    "HookConfig",
    "HookCondition",
    "HookDispatchResult",
    "HookEngine",
    "HookEventContext",
    "HookExecutionControl",
    "HookMatchClause",
    "HookRule",
    "HookToolDecision",
    "ValidationIssue",
    "load_hook_config",
]


def __getattr__(name: str):
    if name == "HookEngine":
        from monkeycode.hooks.engine import HookEngine

        return HookEngine
    if name == "load_hook_config":
        from monkeycode.hooks.config import load_hook_config

        return load_hook_config
    raise AttributeError(name)
