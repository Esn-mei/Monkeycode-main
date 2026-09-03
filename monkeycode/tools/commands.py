from __future__ import annotations

import subprocess
import time
from typing import Any

from monkeycode.tools.base import ToolContext, ToolResult
from monkeycode.tools.base import ToolPolicy


class RunCommandTool:
    name = "run_command"
    description = "Run a shell command in the current workspace and return its output."
    policy = ToolPolicy(tool_name=name, category="command")
    uses_internal_timeout = True
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_seconds": {"type": "number"},
        },
        "required": ["command"],
    }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        command = arguments["command"]
        timeout_seconds = float(arguments.get("timeout_seconds") or context.default_timeout_seconds)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=context.workspace_root,
                shell=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            stdout, stdout_truncated = _truncate(completed.stdout, context.max_output_chars)
            stderr, stderr_truncated = _truncate(completed.stderr, context.max_output_chars)
            return ToolResult(
                tool_name=self.name,
                success=completed.returncode == 0,
                output={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": completed.returncode,
                    "timed_out": False,
                    "duration_ms": duration_ms,
                },
                error_type=None if completed.returncode == 0 else "command_failed",
                error_message=None if completed.returncode == 0 else f"command exited with {completed.returncode}",
                metadata={
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                    "cwd": str(context.workspace_root),
                },
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            stdout, stdout_truncated = _truncate(exc.stdout or "", context.max_output_chars)
            stderr, stderr_truncated = _truncate(exc.stderr or "", context.max_output_chars)
            return ToolResult(
                tool_name=self.name,
                success=False,
                output={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": None,
                    "timed_out": True,
                    "duration_ms": duration_ms,
                },
                error_type="command_timeout",
                error_message=f"command timed out after {timeout_seconds:g} seconds",
                metadata={
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                    "cwd": str(context.workspace_root),
                },
            )


def _truncate(text: str | bytes | None, max_chars: int) -> tuple[str, bool]:
    if text is None:
        text = ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True
