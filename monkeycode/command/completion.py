from __future__ import annotations

from dataclasses import dataclass, field

from monkeycode.command.command import Command
from monkeycode.command.registry import Registry

MAX_ROWS = 8


@dataclass(slots=True)
class CompletionMenu:
    items: list[Command] = field(default_factory=list)
    cursor: int = 0
    offset: int = 0
    active: bool = False

    def update(self, input_text: str, registry: Registry) -> None:
        text = input_text.strip()
        if "\n" in input_text or not text.startswith("/"):
            self.hide()
            return
        self.items = registry.prefix_match(text)
        self.active = True
        self._clamp()

    def move_up(self) -> None:
        if not self.items:
            return
        self.cursor = max(0, self.cursor - 1)
        self._sync_offset()

    def move_down(self) -> None:
        if not self.items:
            return
        self.cursor = min(len(self.items) - 1, self.cursor + 1)
        self._sync_offset()

    def selected(self) -> Command | None:
        if not self.items:
            return None
        return self.items[self.cursor]

    def hide(self) -> None:
        self.items = []
        self.cursor = 0
        self.offset = 0
        self.active = False

    def render(self, width: int = 80) -> str:
        if not self.active:
            return ""
        if not self.items:
            return "无匹配"
        visible = self.items[self.offset : self.offset + MAX_ROWS]
        name_width = max(len(command.name) for command in visible)
        lines: list[str] = []
        if self.offset:
            lines.append(f"↑ {self.offset} more")
        for index, command in enumerate(visible, start=self.offset):
            marker = ">" if index == self.cursor else " "
            line = f"{marker} /{command.name.ljust(name_width)} {command.description}"
            lines.append(line[:width] if width > 0 else line)
        remaining = len(self.items) - (self.offset + len(visible))
        if remaining:
            lines.append(f"↓ {remaining} more")
        return "\n".join(lines)

    def _clamp(self) -> None:
        if not self.items:
            self.cursor = 0
            self.offset = 0
            return
        self.cursor = min(max(self.cursor, 0), len(self.items) - 1)
        self._sync_offset()

    def _sync_offset(self) -> None:
        if self.cursor < self.offset:
            self.offset = self.cursor
        if self.cursor >= self.offset + MAX_ROWS:
            self.offset = self.cursor - MAX_ROWS + 1
