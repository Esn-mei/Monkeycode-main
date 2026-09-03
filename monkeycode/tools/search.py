from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from monkeycode.tools.base import ToolContext, ToolResult
from monkeycode.tools.base import ToolPolicy
from monkeycode.tools.workspace import WorkspaceError, WorkspaceGuard

IGNORED_DIRS = {".git", ".pytest_cache", "__pycache__", "monkeycode.egg-info"}


class FindFilesTool:
    name = "find_files"
    description = (
        "Find file paths in the current workspace by glob pattern. "
        "Use this when the target is identified by file or directory name, such as **/*.py. "
        "Do not use it to search file contents; use search_code when a text query is known."
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
            "pattern": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["pattern"],
    }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = arguments["pattern"]
        max_results = int(arguments.get("max_results") or 100)
        matches: list[str] = []
        for path in _iter_files(context.workspace_root):
            rel = path.relative_to(context.workspace_root).as_posix()
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern):
                matches.append(rel)
                if len(matches) >= max_results:
                    break
        return ToolResult(
            tool_name=self.name,
            success=True,
            output={"files": matches, "count": len(matches), "truncated": len(matches) >= max_results},
        )


class SearchCodeTool:
    name = "search_code"
    description = (
        "Search text content in workspace files and return matching paths, line numbers, and lines. "
        "Use one call with query and path_pattern when the text or symbol to find is known. "
        "Do not call find_files first unless the location cannot be expressed with path_pattern. "
        "Use read_file only after matches are found and surrounding context is required."
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
            "query": {"type": "string"},
            "path_pattern": {"type": "string"},
            "regex": {"type": "boolean"},
            "case_sensitive": {"type": "boolean"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
    }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        query = arguments["query"]
        path_pattern = arguments.get("path_pattern")
        regex = bool(arguments.get("regex", False))
        case_sensitive = bool(arguments.get("case_sensitive", False))
        max_results = int(arguments.get("max_results") or 100)
        flags = 0 if case_sensitive else re.IGNORECASE
        skipped = 0
        results: list[dict[str, Any]] = []
        try:
            pattern = re.compile(query if regex else re.escape(query), flags)
        except re.error as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error_type="invalid_regex",
                error_message=str(exc),
            )

        for path in _iter_files(context.workspace_root):
            rel = path.relative_to(context.workspace_root).as_posix()
            if path_pattern and not (fnmatch.fnmatch(rel, path_pattern) or fnmatch.fnmatch(path.name, path_pattern)):
                continue
            try:
                if _looks_binary(path):
                    skipped += 1
                    continue
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                skipped += 1
                continue

            for line_number, line in enumerate(lines, start=1):
                if pattern.search(line):
                    results.append({"path": rel, "line": line_number, "text": line})
                    if len(results) >= max_results:
                        return _success(self.name, results, skipped, truncated=True)
        return _success(self.name, results, skipped, truncated=False)


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            try:
                WorkspaceGuard(root).resolve(path)
            except WorkspaceError:
                continue
            yield path


def _looks_binary(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:1024]
    except OSError:
        return True
    return b"\x00" in sample


def _success(tool_name: str, results: list[dict[str, Any]], skipped: int, *, truncated: bool) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=True,
        output={
            "matches": results,
            "count": len(results),
            "skipped": skipped,
            "truncated": truncated,
        },
    )
