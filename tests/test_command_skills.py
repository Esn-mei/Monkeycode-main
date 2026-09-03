from __future__ import annotations

import asyncio

from monkeycode.command.registry import Registry
from monkeycode.command.skills import (
    SkillSummary,
    register_skills_as_commands,
    remove_skill_commands,
)
from monkeycode.command.ui import NopUI


class Runner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, ui, name: str, args: str = "") -> None:
        self.calls.append(name)


def test_register_skills_as_commands_binds_each_name() -> None:
    registry = Registry()
    runner = Runner()
    register_skills_as_commands(
        registry,
        [SkillSummary("commit", "Commit"), SkillSummary("test", "Test")],
        runner,
    )

    asyncio.run(registry.lookup("commit").handler(NopUI()))
    asyncio.run(registry.lookup("test").handler(NopUI()))

    assert runner.calls == ["commit", "test"]
    assert [command.description for command in registry.visible()] == [
        "Commit [skill]",
        "Test [skill]",
    ]


def test_remove_skill_commands() -> None:
    registry = Registry()
    register_skills_as_commands(registry, [SkillSummary("commit", "Commit")], Runner())

    remove_skill_commands(registry)

    assert registry.visible() == []
    assert registry.lookup("commit") is None
