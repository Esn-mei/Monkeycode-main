from monkeycode.tools.filter import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    FilterParams,
    apply_agent_tool_filter,
    is_mcp_or_skill,
)


def test_constants() -> None:
    assert "Agent" in ALL_AGENT_DISALLOWED_TOOLS
    assert "read_file" in ASYNC_AGENT_ALLOWED_TOOLS


def test_filter_default_removes_agent() -> None:
    assert apply_agent_tool_filter(FilterParams(["Agent", "read_file"], 0, False)) == ["read_file"]


def test_filter_background_keeps_async_and_mcp() -> None:
    result = apply_agent_tool_filter(
        FilterParams(["Agent", "read_file", "TaskList", "mcp__x"], 0, True)
    )
    assert result == ["read_file", "mcp__x"]


def test_filter_white_and_black_lists() -> None:
    result = apply_agent_tool_filter(
        FilterParams(
            ["read_file", "write_file", "search_code"],
            0,
            False,
            allowed=["read_file", "write_file"],
            disallowed=["write_file"],
        )
    )
    assert result == ["read_file"]


def test_is_mcp_or_skill() -> None:
    assert is_mcp_or_skill("mcp__server__tool") is True
    assert is_mcp_or_skill("read_file") is False
