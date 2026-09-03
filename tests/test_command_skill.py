from __future__ import annotations

import asyncio

from monkeycode.command.builtin_skill import handle_skill
from monkeycode.command.skills import SkillSummary
from monkeycode.command.ui import NopUI


def test_handle_skill_lists_sorted_skills() -> None:
    ui = NopUI()
    ui.catalog_skills = [
        SkillSummary("test", "Run tests"),
        SkillSummary("commit", "Commit changes"),
    ]

    asyncio.run(handle_skill(ui))

    assert ui.messages == [
        "Available skills (2):",
        " /commit Commit changes",
        " /test   Run tests",
        "Type /<skill-name> to invoke a skill.",
    ]


def test_handle_skill_empty() -> None:
    ui = NopUI()

    asyncio.run(handle_skill(ui))

    assert ui.messages == ["No skills loaded."]
