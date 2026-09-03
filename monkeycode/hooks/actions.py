from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from monkeycode.hooks.logging import log_hook_event, redact
from monkeycode.hooks.types import HookActionResult, HookEventContext, HookRule
from monkeycode.messages import ToolCall
from monkeycode.tools.base import ToolResult

MAX_SUBAGENT_CONTEXT_CHARS = 8000


class HookActionRunner:
    def __init__(
        self,
        *,
        tool_executor: Any = None,
        subagent_launcher: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.tool_executor = tool_executor
        self.subagent_launcher = subagent_launcher
        self.logger = logger or logging.getLogger("monkeycode.hooks")

    def run(self, rule: HookRule, context: HookEventContext) -> HookActionResult:
        started = time.monotonic()
        try:
            if rule.action.type == "prompt":
                result = self._run_prompt(rule)
            elif rule.action.type == "command":
                result = self._run_command(rule)
            elif rule.action.type == "http":
                result = self._run_http(rule)
            elif rule.action.type == "subagent":
                result = self._run_subagent(rule, context)
            else:
                result = HookActionResult(
                    success=False,
                    action_type=rule.action.type,
                    error_message=f"unsupported action type: {rule.action.type}",
                )
        except Exception as exc:
            result = HookActionResult(
                success=False,
                action_type=rule.action.type,
                error_message=f"{exc.__class__.__name__}: {exc}",
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        result = HookActionResult(
            success=result.success,
            action_type=result.action_type,
            output=result.output,
            error_message=result.error_message,
            prompt_target=result.prompt_target,
            prompt_content=result.prompt_content,
            duration_ms=result.duration_ms if result.duration_ms is not None else duration_ms,
            background=result.background,
        )
        log_hook_event(
            self.logger,
            event=rule.event,
            rule_id=rule.id,
            action_type=rule.action.type,
            status="success" if result.success else "failed",
            reason=result.error_message,
            duration_ms=result.duration_ms,
            details=result.output if isinstance(result.output, dict) else None,
        )
        return result

    def _run_prompt(self, rule: HookRule) -> HookActionResult:
        content = rule.action.params.get("content", rule.action.params.get("reason", ""))
        text = str(content)
        return HookActionResult(
            success=True,
            action_type="prompt",
            prompt_target=rule.action.target,
            prompt_content=text,
            output={"target": rule.action.target, "content": text},
        )

    def _run_command(self, rule: HookRule) -> HookActionResult:
        if self.tool_executor is None:
            return HookActionResult(
                success=False,
                action_type="command",
                error_message="command action requires a tool executor",
            )
        command = str(rule.action.params.get("command", "")).strip()
        arguments: dict[str, Any] = {"command": command}
        if rule.control.timeout_seconds is not None:
            arguments["timeout_seconds"] = rule.control.timeout_seconds
        call = ToolCall(
            id=f"hook:{rule.id}",
            name="run_command",
            arguments_json=json.dumps(arguments, ensure_ascii=False),
            arguments=arguments,
            provider="hook",
        )
        result: ToolResult = self.tool_executor.execute(call)
        return HookActionResult(
            success=result.success,
            action_type="command",
            output=result.to_dict(),
            error_message=result.error_message,
            duration_ms=_duration_from_tool_result(result),
        )

    def _run_http(self, rule: HookRule) -> HookActionResult:
        method = str(rule.action.params.get("method", "POST")).upper()
        url = str(rule.action.params.get("url", ""))
        headers = rule.action.params.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        body = rule.action.params.get("body", b"")
        data: bytes | None
        request_headers = {str(key): str(value) for key, value in headers.items()}
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        elif body in (None, b""):
            data = None
        else:
            data = str(body).encode("utf-8")
        timeout = rule.control.timeout_seconds or 10.0
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(2048)
                text = raw.decode("utf-8", errors="replace")
                return HookActionResult(
                    success=True,
                    action_type="http",
                    output={
                        "url": url,
                        "method": method,
                        "status": response.status,
                        "response_preview": text,
                    },
                )
        except urllib.error.HTTPError as exc:
            preview = exc.read(2048).decode("utf-8", errors="replace")
            return HookActionResult(
                success=False,
                action_type="http",
                output={"url": url, "method": method, "status": exc.code, "response_preview": preview},
                error_message=f"HTTPError: {exc.code}",
            )
        except Exception as exc:
            return HookActionResult(
                success=False,
                action_type="http",
                output={"url": url, "method": method},
                error_message=f"{exc.__class__.__name__}: {exc}",
            )

    def _run_subagent(self, rule: HookRule, context: HookEventContext) -> HookActionResult:
        if self.subagent_launcher is None:
            return HookActionResult(
                success=False,
                action_type="subagent",
                error_message="subagent action requires a launcher",
            )
        prompt = str(rule.action.params.get("prompt", "")).strip()
        safe_context = json.dumps(
            redact(context.data),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if len(safe_context) > MAX_SUBAGENT_CONTEXT_CHARS:
            safe_context = safe_context[: MAX_SUBAGENT_CONTEXT_CHARS - 16] + "...[truncated]"
        arguments: dict[str, Any] = {
            "prompt": f"{prompt}\n\n<hook_context>\n{safe_context}\n</hook_context>",
            "run_in_background": True,
        }
        for key in ("subagent_type", "name", "model", "isolation"):
            value = rule.action.params.get(key)
            if value not in (None, ""):
                arguments[key] = value
        result: ToolResult = self.subagent_launcher(arguments)
        if not result.success:
            return HookActionResult(
                success=False,
                action_type="subagent",
                output=result.to_dict(),
                error_message=result.error_message,
            )
        output = dict(result.output) if isinstance(result.output, dict) else {"result": result.output}
        output["name"] = str(rule.action.params.get("name") or rule.action.params.get("subagent_type") or "__fork__")
        return HookActionResult(
            success=True,
            action_type="subagent",
            output=output,
        )


def _duration_from_tool_result(result: ToolResult) -> int | None:
    if isinstance(result.output, dict):
        value = result.output.get("duration_ms")
        if isinstance(value, int):
            return value
    value = result.metadata.get("duration_ms")
    return value if isinstance(value, int) else None
