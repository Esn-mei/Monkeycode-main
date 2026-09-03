from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from monkeycode.mcp.client import McpClientError, McpRemoteTool, McpSession
from monkeycode.mcp.config import McpConfig
from monkeycode.mcp.jsonrpc import JsonRpcError
from monkeycode.tools.base import ToolContext, ToolPolicy, ToolResult
from monkeycode.tools.registry import ToolRegistry

TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass
class McpServerDiagnostic:
    server_name: str
    success: bool
    message: str
    tool_count: int = 0


@dataclass
class McpToolManager:
    config: McpConfig
    workspace_root: Path
    sessions: dict[str, McpSession] = field(default_factory=dict)
    diagnostics: list[McpServerDiagnostic] = field(default_factory=list)

    def register_tools(self, registry: ToolRegistry) -> None:
        for server_name, server_config in self.config.servers.items():
            try:
                session = McpSession(server_config, workspace_root=self.workspace_root)
                session.initialize()
                remote_tools = session.list_tools()
                count = 0
                for remote_tool in remote_tools:
                    local_name = make_mcp_tool_name(server_name, remote_tool.name)
                    if registry.get(local_name) is not None:
                        self.diagnostics.append(
                            McpServerDiagnostic(
                                server_name=server_name,
                                success=False,
                                message=f"skipped duplicate MCP tool name {local_name}",
                            )
                        )
                        continue
                    registry.register(McpTool(local_name, server_name, remote_tool, session))
                    count += 1
                self.sessions[server_name] = session
                self.diagnostics.append(
                    McpServerDiagnostic(
                        server_name=server_name,
                        success=True,
                        message="registered MCP tools",
                        tool_count=count,
                    )
                )
            except Exception as exc:
                self.diagnostics.append(
                    McpServerDiagnostic(
                        server_name=server_name,
                        success=False,
                        message=f"{exc.__class__.__name__}: {exc}",
                    )
                )

    def close(self) -> None:
        for session in self.sessions.values():
            session.close()
        self.sessions.clear()


class McpTool:
    def __init__(
        self,
        local_name: str,
        server_name: str,
        remote_tool: McpRemoteTool,
        session: McpSession,
    ) -> None:
        self._name = local_name
        self.server_name = server_name
        self.remote_tool = remote_tool
        self.session = session

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        remote_description = self.remote_tool.description.strip()
        suffix = f"[MCP server: {self.server_name}; remote tool: {self.remote_tool.name}]"
        if remote_description:
            return f"{remote_description}\n\n{suffix}"
        return suffix

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self.remote_tool.input_schema

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(
            tool_name=self.name,
            category="side_effect",
            allowed_in_plan_mode=False,
            can_run_parallel=False,
            has_side_effects=True,
        )

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        session = self.session
        isolated_session: McpSession | None = None
        try:
            session_root = getattr(session, "workspace_root", None)
            if (
                session_root is not None
                and Path(session_root).resolve() != context.workspace_root.resolve()
            ):
                isolated_session = McpSession(
                    session.config,
                    workspace_root=context.workspace_root,
                    timeout_seconds=session.timeout_seconds,
                )
                isolated_session.initialize()
                session = isolated_session
            result = session.call_tool(self.remote_tool.name, arguments)
        except JsonRpcError as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error_type="mcp_tool_error",
                error_message=str(exc),
                metadata=_metadata(self.server_name, self.remote_tool.name),
            )
        except McpClientError as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error_type="mcp_connection_error",
                error_message=str(exc),
                metadata=_metadata(self.server_name, self.remote_tool.name),
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error_type="mcp_tool_error",
                error_message=f"{exc.__class__.__name__}: {exc}",
                metadata=_metadata(self.server_name, self.remote_tool.name),
            )
        finally:
            if isolated_session is not None:
                try:
                    isolated_session.close()
                except Exception:
                    pass

        success = not bool(result.get("isError"))
        output = _tool_output(result)
        return ToolResult(
            tool_name=self.name,
            success=success,
            output=output if success else None,
            error_type=None if success else "mcp_tool_error",
            error_message=None if success else _error_message(output),
            metadata={**_metadata(self.server_name, self.remote_tool.name), "mcp_raw_result": result},
        )


def make_mcp_tool_name(server_name: str, remote_tool_name: str) -> str:
    server = _normalize_name(server_name)
    tool = _normalize_name(remote_tool_name)
    return f"{server}__{tool}"


def _normalize_name(value: str) -> str:
    normalized = TOOL_NAME_RE.sub("_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_-")
    return normalized or "tool"


def _metadata(server_name: str, remote_tool_name: str) -> dict[str, Any]:
    return {
        "mcp_server": server_name,
        "mcp_remote_tool": remote_tool_name,
    }


def _tool_output(result: dict[str, Any]) -> Any:
    if "structuredContent" in result:
        return result["structuredContent"]
    content = result.get("content")
    if not isinstance(content, list):
        return result
    text_parts: list[str] = []
    all_text = True
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text_parts.append(str(item.get("text", "")))
        else:
            all_text = False
    if all_text:
        return "\n".join(text_parts)
    return content


def _error_message(output: Any) -> str:
    if isinstance(output, str) and output.strip():
        return output
    return "MCP tool returned an error"
