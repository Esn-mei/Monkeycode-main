from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from monkeycode.tools.base import ToolContext, ToolPolicy, ToolResult


class SkillProcessTool:
    uses_internal_timeout = True

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        command: list[str],
        base_dir: Path,
    ) -> None:
        self._name = name
        self._description = description
        self._input_schema = input_schema
        self._command = _resolve_command(command, base_dir)
        self._base_dir = base_dir

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(tool_name=self.name, category="side_effect")

    @property
    def is_system(self) -> bool:
        return False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        started = time.monotonic()
        payload = json.dumps(arguments, ensure_ascii=False)
        try:
            completed = subprocess.run(
                self._command,
                cwd=self._base_dir,
                input=payload,
                text=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output={"stdout": exc.stdout or "", "stderr": exc.stderr or ""},
                error_type="tool_timeout",
                error_message="tool timed out after 30 seconds",
                metadata={"duration_ms": int((time.monotonic() - started) * 1000)},
            )
        except OSError as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error_type="tool_exception",
                error_message=f"{exc.__class__.__name__}: {exc}",
                metadata={"duration_ms": int((time.monotonic() - started) * 1000)},
            )

        success = completed.returncode == 0
        output = completed.stdout
        error_message = None
        if not success:
            output = "\n".join(
                part for part in [completed.stdout, completed.stderr] if part
            )
            error_message = f"command exited with {completed.returncode}"
        return ToolResult(
            tool_name=self.name,
            success=success,
            output=output,
            error_type=None if success else "command_failed",
            error_message=error_message,
            metadata={
                "exit_code": completed.returncode,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )


def new_skill_tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    command: list[str],
    base_dir: Path,
) -> SkillProcessTool:
    return SkillProcessTool(
        name=name,
        description=description,
        input_schema=input_schema,
        command=command,
        base_dir=base_dir,
    )


def _resolve_command(command: list[str], base_dir: Path) -> list[str]:
    first = Path(command[0])
    if first.is_absolute():
        resolved = first
    else:
        direct = base_dir / first
        reference = base_dir / "references" / first
        if direct.exists():
            resolved = direct
        elif reference.exists():
            resolved = reference
        else:
            return list(command)
    return [str(resolved), *command[1:]]
