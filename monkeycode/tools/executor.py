from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from monkeycode.messages import ToolCall
from monkeycode.permissions import PermissionManager, PermissionMode, PermissionPrompter, PermissionRuleStore
from monkeycode.tools.base import ToolContext, ToolResult
from monkeycode.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        workspace_root: Path,
        default_timeout_seconds: float = 10.0,
        max_output_chars: int = 12000,
        permission_manager: PermissionManager | None = None,
        permission_mode: PermissionMode | str = PermissionMode.DEFAULT,
        permission_rule_store: PermissionRuleStore | None = None,
        permission_prompter: PermissionPrompter | None = None,
    ) -> None:
        self.registry = registry
        self.context = ToolContext(
            workspace_root=workspace_root.resolve(),
            default_timeout_seconds=default_timeout_seconds,
            max_output_chars=max_output_chars,
        )
        self.permission_manager = permission_manager or PermissionManager(
            mode=permission_mode,
            rule_store=permission_rule_store or PermissionRuleStore.empty(self.context.workspace_root),
            prompter=permission_prompter,
        )
        self._close_callbacks: list[Any] = []

    def add_close_callback(self, callback: Any) -> None:
        self._close_callbacks.append(callback)

    def close(self) -> None:
        while self._close_callbacks:
            callback = self._close_callbacks.pop()
            try:
                callback()
            except Exception:
                continue

    def execute(self, tool_call: ToolCall) -> ToolResult:
        tool = self.registry.get(tool_call.name)
        if tool is None:
            return ToolResult(
                tool_name=tool_call.name,
                success=False,
                error_type="unknown_tool",
                error_message=f"unknown tool: {tool_call.name}",
            )

        try:
            arguments = tool_call.arguments
            if arguments is None:
                parsed = json.loads(tool_call.arguments_json or "{}")
                if not isinstance(parsed, dict):
                    return _error(tool.name, "invalid_arguments", "tool arguments must be a JSON object")
                arguments = parsed
        except json.JSONDecodeError as exc:
            return _error(tool.name, "invalid_json", f"invalid tool arguments JSON: {exc.msg}")

        schema_error = _validate_schema(arguments, tool.parameters_schema)
        if schema_error:
            return _error(tool.name, "schema_validation_failed", schema_error)

        permission = self.permission_manager.authorize(
            tool.name,
            arguments,
            self.registry.policy(tool.name),
            self.context.workspace_root,
        )
        if not permission.allowed:
            return permission.denial_result or _error(
                tool.name,
                "permission_denied",
                "permission denied",
            )

        started = time.monotonic()
        timeout_seconds = _tool_timeout(arguments, self.context.default_timeout_seconds)
        if getattr(tool, "uses_internal_timeout", False):
            try:
                result = tool.execute(arguments, self.context)
            except Exception as exc:
                metadata = permission.decision.metadata(permission.target)
                metadata["duration_ms"] = int((time.monotonic() - started) * 1000)
                return ToolResult(
                    tool_name=tool.name,
                    success=False,
                    error_type="tool_exception",
                    error_message=f"{exc.__class__.__name__}: {exc}",
                    metadata=metadata,
                )
            metadata = dict(result.metadata)
            metadata.update(permission.decision.metadata(permission.target))
            metadata.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
            return ToolResult(
                tool_name=result.tool_name,
                success=result.success,
                output=result.output,
                error_type=result.error_type,
                error_message=result.error_message,
                metadata=metadata,
            )

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(tool.execute, arguments, self.context)
            try:
                result = future.result(timeout=timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                metadata = permission.decision.metadata(permission.target)
                metadata["duration_ms"] = int((time.monotonic() - started) * 1000)
                return ToolResult(
                    tool_name=tool.name,
                    success=False,
                    error_type="tool_timeout",
                    error_message=f"tool timed out after {timeout_seconds:g} seconds",
                    metadata=metadata,
                )
        except Exception as exc:
            metadata = permission.decision.metadata(permission.target)
            metadata["duration_ms"] = int((time.monotonic() - started) * 1000)
            return ToolResult(
                tool_name=tool.name,
                success=False,
                error_type="tool_exception",
                error_message=f"{exc.__class__.__name__}: {exc}",
                metadata=metadata,
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        metadata = dict(result.metadata)
        metadata.update(permission.decision.metadata(permission.target))
        metadata.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
        return ToolResult(
            tool_name=result.tool_name,
            success=result.success,
            output=result.output,
            error_type=result.error_type,
            error_message=result.error_message,
            metadata=metadata,
        )


def _error(tool_name: str, error_type: str, message: str) -> ToolResult:
    return ToolResult(tool_name=tool_name, success=False, error_type=error_type, error_message=message)


def _validate_schema(arguments: dict[str, Any], schema: dict[str, Any]) -> str | None:
    if schema.get("type") != "object":
        return "tool parameters schema must be an object"

    required = schema.get("required", [])
    for key in required:
        if key not in arguments:
            return f"missing required argument: {key}"

    properties = schema.get("properties", {})
    for key, value in arguments.items():
        prop = properties.get(key)
        if not prop:
            continue
        expected = prop.get("type")
        if expected and not _matches_type(value, expected):
            return f"argument {key!r} must be {expected}"
    return None


def _matches_type(value: Any, expected: str | list[str]) -> bool:
    expected_types = expected if isinstance(expected, list) else [expected]
    for expected_type in expected_types:
        if expected_type == "string" and isinstance(value, str):
            return True
        if expected_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if expected_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if expected_type == "boolean" and isinstance(value, bool):
            return True
        if expected_type == "object" and isinstance(value, dict):
            return True
        if expected_type == "array" and isinstance(value, list):
            return True
        if expected_type == "null" and value is None:
            return True
    return False


def _tool_timeout(arguments: dict[str, Any], default_timeout_seconds: float) -> float:
    value = arguments.get("timeout_seconds", default_timeout_seconds)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return default_timeout_seconds
