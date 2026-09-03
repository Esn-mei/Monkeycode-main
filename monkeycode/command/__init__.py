from __future__ import annotations

from monkeycode.command.builtins import register_builtins
from monkeycode.command.command import Command, Handler, Kind
from monkeycode.command.completion import CompletionMenu
from monkeycode.command.dispatch import parse
from monkeycode.command.registry import Registry
from monkeycode.command.skills import SkillSummary, register_skills_as_commands, remove_skill_commands
from monkeycode.command.ui import NopUI, UI

__all__ = [
    "Command",
    "CompletionMenu",
    "Handler",
    "Kind",
    "NopUI",
    "Registry",
    "SkillSummary",
    "UI",
    "parse",
    "register_builtins",
    "register_skills_as_commands",
    "remove_skill_commands",
]
