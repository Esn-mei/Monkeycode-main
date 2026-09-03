from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from monkeycode.mcp.config import McpServerConfig
from monkeycode.mcp.jsonrpc import JsonRpcError, JsonRpcProtocol, extract_result
from monkeycode.mcp.transports import McpTransport, McpTransportError, create_transport

DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0


class McpClientError(Exception):
    pass


@dataclass(frozen=True)
class McpRemoteTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    raw: dict[str, Any]


class McpSession:
    def __init__(
        self,
        config: McpServerConfig,
        *,
        workspace_root: Path,
        transport: McpTransport | None = None,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config
        self.workspace_root = workspace_root.resolve()
        self.transport = transport or create_transport(config, cwd=self.workspace_root)
        self.timeout_seconds = timeout_seconds
        self.protocol = JsonRpcProtocol()
        self.initialized = False

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "MonkeyCode",
                    "version": "0.1.0",
                },
            },
        )
        self.initialized = True
        self.notify("notifications/initialized")
        return result if isinstance(result, dict) else {}

    def list_tools(self) -> list[McpRemoteTool]:
        result = self.request("tools/list", {})
        if not isinstance(result, dict):
            raise McpClientError(f"MCP server {self.config.name} returned invalid tools/list result")
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise McpClientError(f"MCP server {self.config.name} returned invalid tools list")
        remote_tools: list[McpRemoteTool] = []
        for raw_tool in tools:
            if not isinstance(raw_tool, dict):
                continue
            name = raw_tool.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            description = raw_tool.get("description")
            schema = raw_tool.get("inputSchema")
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            if schema.get("type") != "object":
                schema = {"type": "object", "properties": {}, "x-mcp-original-schema": schema}
            remote_tools.append(
                McpRemoteTool(
                    name=name.strip(),
                    description=str(description or ""),
                    input_schema=schema,
                    raw=raw_tool,
                )
            )
        return remote_tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise McpClientError(f"MCP server {self.config.name} returned invalid tools/call result")
        return result

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id, payload = self.protocol.next_request(method, params)
        try:
            message = self.transport.request(payload, request_id, self.timeout_seconds)
            return extract_result(message, request_id)
        except JsonRpcError:
            raise
        except McpTransportError as exc:
            raise McpClientError(str(exc)) from exc

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload = self.protocol.notification(method, params)
        try:
            self.transport.notify(payload, self.timeout_seconds)
        except McpTransportError:
            # Initialized notifications are best-effort in v1.
            return

    def close(self) -> None:
        self.transport.close()
