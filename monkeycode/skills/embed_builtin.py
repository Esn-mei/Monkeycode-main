from __future__ import annotations

from importlib import resources
from importlib.abc import Traversable
from pathlib import Path


def iter_builtin_skill_dirs() -> list[Traversable]:
    base = resources.files("monkeycode.skills.builtin")
    return [
        entry
        for entry in base.iterdir()
        if entry.is_dir() and entry.joinpath("SKILL.md").is_file()
    ]


def copy_builtin_skill_dir(entry: Traversable, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in entry.iterdir():
        destination = target / child.name
        if child.is_dir():
            copy_builtin_skill_dir(child, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(child.read_bytes())
