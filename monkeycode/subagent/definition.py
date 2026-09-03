from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from monkeycode.permissions import PermissionMode


class Source(IntEnum):
    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3

    def __str__(self) -> str:
        return {
            Source.BUILTIN: "builtin",
            Source.USER: "user",
            Source.PROJECT: "project",
            Source.PLUGIN: "plugin",
        }.get(self, "unknown")


@dataclass(frozen=True)
class Definition:
    """子 Agent 定义。

    name: Agent 名称；description: 选择该 Agent 时展示给主 Agent 的说明；
    tools: 可选工具白名单；disallowed_tools: 工具黑名单；model: 模型偏好；
    max_turns: 子 Agent 最大循环轮数，0 表示沿用默认值；
    permission_mode: 子 Agent 权限模式；dont_ask: 是否自动批准 Ask；
    background: 是否默认后台执行；system_prompt: 角色提示词正文；
    file_path: 定义来源路径；source: 定义来源层级。
    """

    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: str = "inherit"
    max_turns: int = 0
    permission_mode: PermissionMode | None = None
    dont_ask: bool = False
    background: bool = False
    isolation: str = "none"
    system_prompt: str = ""
    file_path: str | Path = ""
    source: Source = Source.BUILTIN

    def is_fork(self) -> bool:
        return self.name == "__fork__"
