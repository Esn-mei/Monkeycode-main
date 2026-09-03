from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from monkeycode.events import AgentMode, CancellationToken
from monkeycode.mcp.client import McpRemoteTool
from monkeycode.mcp.config import McpConfig, McpServerConfig
from monkeycode.mcp.tools import McpTool, McpToolManager
from monkeycode.messages import ToolCall
from monkeycode.permissions import PermissionMode
from monkeycode.tool_scheduler import ToolScheduler
from monkeycode.tools import create_default_registry
from monkeycode.tools.executor import ToolExecutor
from monkeycode.tools.registry import ToolRegistry


def test_fake_stdio_mcp_server_registers_and_calls_tool(tmp_path: Path) -> None:
    script = _write_fake_stdio_server(tmp_path)
    config = McpConfig(
        {
            "fake": McpServerConfig(
                name="fake",
                transport="stdio",
                command=sys.executable,
                args=["-u", str(script)],
            )
        }
    )
    registry = create_default_registry()
    manager = McpToolManager(config, tmp_path)

    try:
        manager.register_tools(registry)
        executor = ToolExecutor(registry, workspace_root=tmp_path, permission_mode=PermissionMode.ALLOW)
        result = executor.execute(
            ToolCall(
                id="call_1",
                name="fake__echo",
                arguments={"text": "hello"},
                arguments_json='{"text":"hello"}',
            )
        )
    finally:
        manager.close()

    assert registry.get("fake__echo") is not None
    assert result.success is True
    assert result.output == "echo: hello"


def test_fake_http_mcp_server_supports_json_and_sse(tmp_path: Path) -> None:
    server = _FakeHttpMcpServer()
    try:
        json_config = McpConfig(
            {
                "json": McpServerConfig(
                    name="json",
                    transport="http",
                    url=f"{server.url}/json",
                ),
                "sse": McpServerConfig(
                    name="sse",
                    transport="http",
                    url=f"{server.url}/sse",
                ),
            }
        )
        registry = ToolRegistry()
        manager = McpToolManager(json_config, tmp_path)
        manager.register_tools(registry)
        executor = ToolExecutor(registry, workspace_root=tmp_path, permission_mode=PermissionMode.ALLOW)

        json_result = executor.execute(
            ToolCall(id="a", name="json__echo", arguments={"text": "one"}, arguments_json='{"text":"one"}')
        )
        sse_result = executor.execute(
            ToolCall(id="b", name="sse__echo", arguments={"text": "two"}, arguments_json='{"text":"two"}')
        )
    finally:
        manager.close()
        server.close()

    assert json_result.success is True
    assert json_result.output == "echo: one"
    assert sse_result.success is True
    assert sse_result.output == "echo: two"
    assert "2025-06-18" in server.protocol_versions


def test_mcp_manager_skips_failed_server_without_breaking_other_tools(tmp_path: Path) -> None:
    script = _write_fake_stdio_server(tmp_path)
    config = McpConfig(
        {
            "bad": McpServerConfig(
                name="bad",
                transport="stdio",
                command=str(tmp_path / "missing-command.exe"),
            ),
            "good": McpServerConfig(
                name="good",
                transport="stdio",
                command=sys.executable,
                args=["-u", str(script)],
            ),
        }
    )
    registry = create_default_registry()
    manager = McpToolManager(config, tmp_path)

    try:
        manager.register_tools(registry)
    finally:
        manager.close()

    assert registry.get("good__echo") is not None
    assert registry.get("read_file") is not None
    assert any(not item.success and item.server_name == "bad" for item in manager.diagnostics)


def test_mcp_tool_is_rejected_in_plan_mode(tmp_path: Path) -> None:
    remote = McpRemoteTool(name="echo", description="", input_schema={"type": "object"}, raw={})
    registry = ToolRegistry()
    registry.register(McpTool("fake__echo", "fake", remote, _NeverCalledSession()))
    scheduler = ToolScheduler(
        ToolExecutor(registry, workspace_root=tmp_path, permission_mode=PermissionMode.ALLOW)
    )

    list(
        scheduler.run_tool_calls(
            [ToolCall(id="call_1", name="fake__echo", arguments={}, arguments_json="{}")],
            mode=AgentMode.PLAN,
            cancel_token=CancellationToken(),
            iteration=1,
        )
    )

    assert scheduler.results[0][1].error_type == "tool_not_allowed_in_plan_mode"


def _write_fake_stdio_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(
        r'''
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "notifications/initialized":
        continue
    request_id = request.get("id")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake"}}
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo input.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                }
            ]
        }
    elif method == "tools/call":
        text = request.get("params", {}).get("arguments", {}).get("text", "")
        result = {"content": [{"type": "text", "text": "echo: " + text}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "missing"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
'''.lstrip(),
        encoding="utf-8",
    )
    return script


class _NeverCalledSession:
    def call_tool(self, name, arguments):
        raise AssertionError("Plan Mode should reject before tool execution")


class _FakeHttpMcpServer:
    def __init__(self) -> None:
        self.protocol_versions: list[str] = []
        self.server = HTTPServer(("127.0.0.1", 0), self._handler_class())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _handler_class(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                parent.protocol_versions.append(self.headers.get("MCP-Protocol-Version", ""))
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                if "id" not in request:
                    self.send_response(202)
                    self.end_headers()
                    return
                message = self._response_for(request)
                if self.path.endswith("/sse"):
                    body = f"event: message\ndata: {json.dumps(message)}\n\n".encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = json.dumps(message).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

            def _response_for(self, request):
                method = request.get("method")
                request_id = request.get("id")
                if method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {}}
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo input.",
                                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                            }
                        ]
                    }
                elif method == "tools/call":
                    text = request.get("params", {}).get("arguments", {}).get("text", "")
                    result = {"content": [{"type": "text", "text": "echo: " + text}]}
                else:
                    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "missing"}}
                return {"jsonrpc": "2.0", "id": request_id, "result": result}

        return Handler
