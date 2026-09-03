from __future__ import annotations

from monkeycode.command.builtin_local import (
    handle_memory,
    handle_permission,
    handle_session,
    handle_status,
    make_help_handler,
)
from monkeycode.command.builtin_prompt import handle_do
from monkeycode.command.builtin_skill import handle_skill
from monkeycode.command.builtin_ui import (
    handle_cancel,
    handle_clear,
    handle_compact,
    handle_default,
    handle_exit,
    handle_plan,
    handle_resume,
)
from monkeycode.command.command import Command, Kind
from monkeycode.command.registry import Registry


def register_builtins(registry: Registry) -> None:
    commands = [
        Command("clear", "清空当前会话并开启新 session", Kind.UI, handle_clear),
        Command("compact", "手动触发上下文压缩", Kind.UI, handle_compact),
        Command("do", "按已确认计划继续执行", Kind.PROMPT, handle_do),
        Command("exit", "退出 MonkeyCode", Kind.UI, handle_exit, aliases=["quit"]),
        Command("help", "显示可用命令", Kind.LOCAL, make_help_handler(registry)),
        Command("memory", "显示已加载的记忆文件", Kind.LOCAL, handle_memory),
        Command("permission", "显示当前权限模式", Kind.LOCAL, handle_permission),
        Command("plan", "切换到计划模式", Kind.UI, handle_plan),
        Command("resume", "从历史会话恢复", Kind.UI, handle_resume),
        Command("session", "显示当前会话信息", Kind.LOCAL, handle_session),
        Command("skill", "列出已加载的 Skill", Kind.LOCAL, handle_skill),
        Command("status", "显示当前运行状态", Kind.LOCAL, handle_status),
        Command("cancel", "取消当前任务", Kind.UI, handle_cancel, hidden=True),
        Command("default", "切换到执行模式", Kind.UI, handle_default, hidden=True),
    ]
    for command in commands:
        registry.register(command)
