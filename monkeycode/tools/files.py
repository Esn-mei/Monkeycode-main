from __future__ import annotations

from pathlib import Path
from typing import Any

from monkeycode.tools.base import ToolContext, ToolResult
from monkeycode.tools.base import ToolPolicy
from monkeycode.tools.workspace import WorkspaceError, WorkspaceGuard


class ReadFileTool:
    name = "read_file"
    description = (
        "Read the complete text of one known workspace file. "
        "Use this when the exact path is already known and full context is needed. "
        "Do not use it merely to discover files or collect matching lines; use find_files or "
        "search_code for those tasks."
    )
    policy = ToolPolicy(
        tool_name=name,
        category="read",
        allowed_in_plan_mode=True,
        can_run_parallel=True,
        has_side_effects=False,
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "encoding": {"type": "string"},
            "max_bytes": {"type": "integer"},
        },
        "required": ["path"],
    }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = arguments["path"]
        encoding = arguments.get("encoding", "utf-8")
        max_bytes = int(arguments.get("max_bytes") or context.max_output_chars)
        try:
            target = _file_path(context, path)
            data = target.read_bytes()
            truncated = len(data) > max_bytes
            content = data[:max_bytes].decode(encoding, errors="replace")
            return ToolResult(
                tool_name=self.name,
                success=True,
                output={
                    "path": _relative(context, target),
                    "content": content,
                    "bytes": len(data),
                    "truncated": truncated,
                },
            )
        except WorkspaceError as exc:
            return _failure(self.name, "path_outside_workspace", str(exc))
        except FileNotFoundError:
            return _failure(self.name, "file_not_found", f"file not found: {path}")
        except IsADirectoryError:
            return _failure(self.name, "is_directory", f"path is a directory: {path}")
        except OSError as exc:
            return _failure(self.name, "read_failed", f"{exc.__class__.__name__}: {exc}")


class WriteFileTool:
    name = "write_file"
    description = "Create or overwrite a text file inside the current workspace."
    policy = ToolPolicy(tool_name=name, category="write")
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "encoding": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = arguments["path"]
        content = arguments["content"]
        encoding = arguments.get("encoding", "utf-8")
        try:
            target = WorkspaceGuard(context.workspace_root).resolve_writable(path)
            if target.exists() and target.is_dir():
                return _failure(self.name, "is_directory", f"path is a directory: {path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode(encoding)
            target.write_bytes(data)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output={"path": _relative(context, target), "bytes": len(data)},
            )
        except WorkspaceError as exc:
            return _failure(self.name, "path_outside_workspace", str(exc))
        except OSError as exc:
            return _failure(self.name, "write_failed", f"{exc.__class__.__name__}: {exc}")


class EditFileTool:
    name = "edit_file"
    description = "Replace one unique text occurrence in a workspace file."
    policy = ToolPolicy(tool_name=name, category="write")
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "encoding": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = arguments["path"]
        old_text = arguments["old_text"]
        new_text = arguments["new_text"]
        encoding = arguments.get("encoding", "utf-8")
        try:
            target = _file_path(context, path)
            content = target.read_text(encoding=encoding)
            count = content.count(old_text)
            if count == 0:
                return _failure(
                    self.name,
                    "old_text_not_found",
                    "old_text was not found; file was not modified",
                    {"match_count": count},
                )
            if count > 1:
                return _failure(
                    self.name,
                    "old_text_not_unique",
                    "old_text matched multiple times; file was not modified",
                    {"match_count": count},
                )
            updated = content.replace(old_text, new_text, 1)
            target.write_text(updated, encoding=encoding)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output={"path": _relative(context, target), "replacements": 1},
            )
        except WorkspaceError as exc:
            return _failure(self.name, "path_outside_workspace", str(exc))
        except FileNotFoundError:
            return _failure(self.name, "file_not_found", f"file not found: {path}")
        except IsADirectoryError:
            return _failure(self.name, "is_directory", f"path is a directory: {path}")
        except OSError as exc:
            return _failure(self.name, "edit_failed", f"{exc.__class__.__name__}: {exc}")


def _file_path(context: ToolContext, path: str) -> Path:
    target = WorkspaceGuard(context.workspace_root).resolve(path)
    if target.is_dir():
        raise IsADirectoryError(path)
    return target


def _relative(context: ToolContext, target: Path) -> str:
    return target.resolve().relative_to(context.workspace_root).as_posix()


def _failure(
    tool_name: str,
    error_type: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=False,
        error_type=error_type,
        error_message=message,
        metadata=metadata or {},
    )
