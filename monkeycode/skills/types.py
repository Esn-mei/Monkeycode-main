from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class SkillSource(Enum):
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"

    def __str__(self) -> str:
        return self.value


@dataclass
class SkillMeta:
    name: str
    description: str
    allowed_tools: list[str] = field(default_factory=list)
    mode: Literal["inline", "fork"] = "inline"
    fork_context: Literal["none", "recent", "full"] = "none"
    model: str | None = None

    def is_fork(self) -> bool:
        return self.mode == "fork"


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    command: list[str]
    base_dir: Path


@dataclass
class Skill:
    meta: SkillMeta
    prompt_body: str
    source_dir: Path
    source: SkillSource
    tool_specs: list[ToolSpec] = field(default_factory=list)


@dataclass(frozen=True)
class ActiveEntry:
    name: str
    body: str
