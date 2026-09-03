from __future__ import annotations

from pathlib import Path

from monkeycode.mcp.client import McpRemoteTool
from monkeycode.mcp.tools import McpTool, make_mcp_tool_name
from monkeycode.tools.base import ToolContext


class FakeSession:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def test_mcp_tool_name_uses_server_prefix_and_normalizes() -> None:
    assert make_mcp_tool_name("docs", "search") == "docs__search"
    assert make_mcp_tool_name("my server", "read.file") == "my_server__read_file"


def test_mcp_tool_maps_schema_description_and_success_result(tmp_path) -> None:
    remote = McpRemoteTool(
        name="search",
        description="Search docs.",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        raw={},
    )
    session = FakeSession({"content": [{"type": "text", "text": "hello"}]})
    tool = McpTool("docs__search", "docs", remote, session)

    result = tool.execute({"query": "hi"}, ToolContext(workspace_root=tmp_path))

    assert "Search docs." in tool.description
    assert "MCP server: docs" in tool.description
    assert tool.parameters_schema["properties"]["query"]["type"] == "string"
    assert result.success is True
    assert result.output == "hello"
    assert result.metadata["mcp_server"] == "docs"
    assert session.calls == [("search", {"query": "hi"})]


def test_mcp_tool_maps_remote_error(tmp_path) -> None:
    remote = McpRemoteTool(name="write", description="", input_schema={"type": "object"}, raw={})
    session = FakeSession({"isError": True, "content": [{"type": "text", "text": "denied"}]})
    tool = McpTool("fs__write", "fs", remote, session)

    result = tool.execute({}, ToolContext(workspace_root=tmp_path))

    assert result.success is False
    assert result.error_type == "mcp_tool_error"
    assert result.error_message == "denied"


def test_mcp_tool_restarts_session_with_tool_context_cwd(tmp_path, monkeypatch) -> None:
    parent_root = tmp_path / "parent"
    child_root = tmp_path / "child"
    parent_root.mkdir()
    child_root.mkdir()

    class ParentSession:
        config = object()
        timeout_seconds = 3.0
        workspace_root = parent_root
        calls = []

        def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            raise AssertionError("parent MCP session must not serve an isolated worktree")

    class IsolatedSession:
        instances = []

        def __init__(self, config, *, workspace_root, timeout_seconds):
            self.workspace_root = Path(workspace_root).resolve()
            self.initialized = False
            self.closed = False
            self.calls = []
            self.instances.append(self)

        def initialize(self):
            self.initialized = True

        def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return {"content": [{"type": "text", "text": "isolated"}]}

        def close(self):
            self.closed = True

    monkeypatch.setattr("monkeycode.mcp.tools.McpSession", IsolatedSession)
    remote = McpRemoteTool(
        name="read",
        description="",
        input_schema={"type": "object"},
        raw={},
    )
    parent = ParentSession()
    tool = McpTool("fs__read", "fs", remote, parent)

    result = tool.execute({}, ToolContext(workspace_root=child_root))

    isolated = IsolatedSession.instances[0]
    assert result.success is True
    assert result.output == "isolated"
    assert parent.calls == []
    assert isolated.workspace_root == child_root.resolve()
    assert isolated.initialized is True
    assert isolated.closed is True
