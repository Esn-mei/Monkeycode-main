from __future__ import annotations

from dataclasses import dataclass, field

ALL_AGENT_DISALLOWED_TOOLS = ["Agent"]
CUSTOM_AGENT_DISALLOWED_TOOLS: list[str] = []
ASYNC_AGENT_ALLOWED_TOOLS = [
    "read_file",
    "write_file",
    "edit_file",
    "find_files",
    "search_code",
    "run_command",
    "load_skill",
    "install_skill",
]


@dataclass(frozen=True)
class FilterParams:
    all: list[str]
    source: int
    background: bool
    allowed: list[str] = field(default_factory=list)
    disallowed: list[str] = field(default_factory=list)


def apply_agent_tool_filter(params: FilterParams) -> list[str]:
    names = list(params.all)
    names = [name for name in names if name not in set(ALL_AGENT_DISALLOWED_TOOLS)]
    if params.source >= 2 and CUSTOM_AGENT_DISALLOWED_TOOLS:
        names = [name for name in names if name not in set(CUSTOM_AGENT_DISALLOWED_TOOLS)]
    if params.background:
        async_allowed = set(ASYNC_AGENT_ALLOWED_TOOLS)
        names = [name for name in names if name in async_allowed or is_mcp_or_skill(name)]
    if params.disallowed:
        blocked = set(params.disallowed)
        names = [name for name in names if name not in blocked]
    if params.allowed:
        allowed = set(params.allowed)
        names = [name for name in names if name in allowed]
    return names


def is_mcp_or_skill(name: str) -> bool:
    return name.startswith("mcp__")
