from __future__ import annotations

from collections.abc import Callable

from monkeycode.command.command import Command


class Registry:
    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._visible: list[Command] = []

    def register(self, cmd: Command) -> None:
        keys = [cmd.name, *cmd.aliases]
        seen: set[str] = set()
        for key in keys:
            if not key or key != key.lower():
                raise ValueError(f"command name must be non-empty lowercase: {key!r}")
            if key in seen:
                raise RuntimeError(f"command conflict: {key}")
            seen.add(key)
            if key in self._by_name:
                raise RuntimeError(f"command conflict: {key}")
        for key in keys:
            self._by_name[key] = cmd
        if not cmd.hidden:
            self._visible.append(cmd)
            self._visible.sort(key=lambda command: command.name)

    def lookup(self, name: str) -> Command | None:
        return self._by_name.get(name.lower())

    def visible(self) -> list[Command]:
        return list(self._visible)

    def prefix_match(self, prefix: str) -> list[Command]:
        normalized = prefix.lstrip("/").lower()
        if not normalized:
            return self.visible()
        return [command for command in self._visible if command.name.startswith(normalized)]

    def remove_if(self, pred: Callable[[Command], bool]) -> None:
        remove_commands = {command.name for command in self._visible if pred(command)}
        seen: set[str] = set()
        for command in self._by_name.values():
            if command.name in seen:
                continue
            seen.add(command.name)
            if pred(command):
                remove_commands.add(command.name)
        self._visible = [command for command in self._visible if command.name not in remove_commands]
        self._by_name = {
            key: command
            for key, command in self._by_name.items()
            if command.name not in remove_commands
        }
