from __future__ import annotations

from monkeycode.skills.active import ActiveSkills
from monkeycode.skills.catalog import Catalog, ValidationIssue
from monkeycode.skills.types import ActiveEntry, Skill, SkillMeta, SkillSource, ToolSpec

__all__ = [
    "ActiveEntry",
    "ActiveSkills",
    "Catalog",
    "Executor",
    "Skill",
    "SkillMeta",
    "SkillSource",
    "ToolSpec",
    "ValidationIssue",
]


def __getattr__(name: str):
    if name == "Executor":
        from monkeycode.skills.executor import Executor

        return Executor
    raise AttributeError(name)
