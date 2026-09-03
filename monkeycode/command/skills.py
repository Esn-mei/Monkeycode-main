from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from monkeycode.command.command import Command, Kind
from monkeycode.command.registry import Registry


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    source: str = ""
    mode: str = ""


class SkillRunner(Protocol):
    async def execute(self, ui, name: str, args: str = "") -> None: ...


def register_skills_as_commands(
    registry: Registry,
    items: list[SkillSummary],
    executor: SkillRunner,
) -> None:
    for item in sorted(items, key=lambda value: value.name):

        async def _handler(ui, *, skill_name: str = item.name) -> None:
            await executor.execute(ui, skill_name, "")

        registry.register(
            Command(
                name=item.name,
                description=f"{item.description} [skill]",
                kind=Kind.PROMPT,
                handler=_handler,
                is_skill=True,
            )
        )


def remove_skill_commands(registry: Registry) -> None:
    registry.remove_if(lambda command: command.is_skill)
