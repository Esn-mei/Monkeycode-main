from __future__ import annotations

from monkeycode.command.command import Handler
from monkeycode.command.registry import Registry


def make_help_handler(registry: Registry) -> Handler:
    async def _handler(ui) -> None:
        commands = registry.visible()
        if not commands:
            ui.println("无可用命令")
            return
        width = max(len(command.name) for command in commands)
        lines = [f"/{command.name.ljust(width)} {command.description}" for command in commands]
        ui.println("\n".join(lines))

    return _handler


async def handle_status(ui) -> None:
    rows = [
        ("Mode", ui.mode().value),
        ("Tokens", f"{ui.usage_in()} in / {ui.usage_out()} out / {ui.usage_total()} total"),
        ("Tools", f"{ui.tool_count()} enabled"),
        ("Memories", f"{len(ui.memory_files())} files"),
        ("Model", ui.model_name()),
        ("Directory", ui.cwd()),
    ]
    width = max(len(key) for key, _ in rows)
    body = "\n".join(f"{key.ljust(width)}: {value}" for key, value in rows)
    ui.println(f"MonkeyCode Status\n\n{body}")


async def handle_memory(ui) -> None:
    files = ui.memory_files()
    if not files:
        ui.println("无已加载的记忆文件")
        return
    ui.println("\n".join(files))


async def handle_permission(ui) -> None:
    ui.println(ui.mode().value)


async def handle_session(ui) -> None:
    ui.println(f"Session: {ui.session_id()}\nPath: {ui.session_path()}")
