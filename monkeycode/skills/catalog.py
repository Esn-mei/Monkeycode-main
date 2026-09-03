from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from monkeycode.skills.embed_builtin import (
    copy_builtin_skill_dir,
    iter_builtin_skill_dirs,
)
from monkeycode.skills.parser import parse_skill_dir
from monkeycode.skills.types import Skill, SkillSource

SYSTEM_TOOL_NAMES = {"load_skill"}


@dataclass(frozen=True)
class ValidationIssue:
    skill_name: str
    tool_name: str


class Catalog:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_name: dict[str, Skill] = {}
        self._order: list[str] = []

    @classmethod
    def load(cls, work_dir: Path) -> Catalog:
        catalog = cls()
        _load_builtin_into(catalog)
        _load_dir_into(catalog, _user_skills_dir(), SkillSource.USER)
        _load_dir_into(
            catalog, work_dir / ".monkeycode" / "skills", SkillSource.PROJECT
        )
        return catalog

    def reload(self, work_dir: Path) -> None:
        fresh = Catalog.load(work_dir)
        with self._lock:
            self._by_name = fresh._by_name
            self._order = fresh._order

    def register(self, skill: Skill) -> None:
        with self._lock:
            self._by_name[skill.meta.name] = skill
            if skill.meta.name not in self._order:
                self._order.append(skill.meta.name)
            self._order.sort()

    def get(self, name: str) -> Skill | None:
        with self._lock:
            return self._by_name.get(name)

    def list(self) -> list[Skill]:
        with self._lock:
            return [
                self._by_name[name] for name in self._order if name in self._by_name
            ]

    def names(self) -> list[str]:
        with self._lock:
            return list(self._order)

    def remove(self, name: str) -> None:
        with self._lock:
            self._by_name.pop(name, None)
            self._order = [item for item in self._order if item != name]

    def remove_many(self, names: set[str]) -> None:
        for name in names:
            self.remove(name)

    def validate_tools(self, registry) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for skill in self.list():
            for tool_name in skill.meta.allowed_tools:
                if (
                    tool_name in SYSTEM_TOOL_NAMES
                    or registry.get(tool_name) is not None
                ):
                    continue
                issues.append(
                    ValidationIssue(skill_name=skill.meta.name, tool_name=tool_name)
                )
        return issues


def _load_dir_into(catalog: Catalog, base_dir: Path, source: SkillSource) -> None:
    if not base_dir.is_dir():
        return
    for child in sorted(base_dir.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        try:
            catalog.register(parse_skill_dir(child, source))
        except Exception as exc:
            print(f"skill {child.name}: {exc}, skipped", file=sys.stderr)


def _load_builtin_into(catalog: Catalog) -> None:
    cache_base = _cache_dir() / "monkeycode" / "builtin-skills"
    for entry in iter_builtin_skill_dirs():
        target = cache_base / entry.name
        try:
            copy_builtin_skill_dir(entry, target)
            catalog.register(parse_skill_dir(target, SkillSource.BUILTIN))
        except Exception as exc:
            try:
                catalog.register(parse_skill_dir(entry, SkillSource.BUILTIN))
            except Exception:
                print(f"builtin skill {entry.name}: {exc}, skipped", file=sys.stderr)


def _cache_dir() -> Path:
    value = os.environ.get("XDG_CACHE_HOME")
    if value:
        return Path(value)
    return _home_dir() / ".cache"


def _user_skills_dir() -> Path:
    return _home_dir() / ".monkeycode" / "skills"


def _home_dir() -> Path:
    value = os.environ.get("MONKEYCODE_HOME") or os.environ.get("HOME")
    if value:
        return Path(value)
    return Path.home()
