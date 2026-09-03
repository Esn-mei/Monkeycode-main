from __future__ import annotations

import os
import sys
from pathlib import Path

from monkeycode.subagent.definition import Definition, Source
from monkeycode.subagent.embed import builtin_definitions
from monkeycode.subagent.parser import parse_file


class Catalog:
    def __init__(self) -> None:
        self._by_name: dict[str, Definition] = {}
        self._by_source: dict[Source, list[Definition]] = {source: [] for source in Source}

    def add(self, definition: Definition) -> None:
        self._by_name[definition.name] = definition
        self._by_source.setdefault(definition.source, []).append(definition)

    def _add_all(self, definitions: list[Definition]) -> None:
        for definition in definitions:
            self.add(definition)

    def resolve(self, name: str) -> Definition | None:
        if not name:
            return self.fork_definition()
        return self._by_name.get(name)

    def list(self) -> list[Definition]:
        return sorted(self._by_name.values(), key=lambda item: item.name)

    def list_by_source(self, source: Source) -> list[Definition]:
        return sorted(self._by_source.get(source, []), key=lambda item: item.name)

    def fork_definition(self) -> Definition:
        return Definition(
            name="__fork__",
            description="Fork-based subagent",
            model="inherit",
            max_turns=25,
            source=Source.BUILTIN,
        )


def load_catalog(root: str | Path) -> Catalog:
    catalog = Catalog()
    catalog._add_all(builtin_definitions())
    catalog._add_all(_load_from_dir(_home_dir() / ".monkeycode" / "agents", Source.USER))
    catalog._add_all(_load_from_dir(Path(root) / ".monkeycode" / "agents", Source.PROJECT))
    return catalog


def _load_from_dir(dir_path: Path, source: Source) -> list[Definition]:
    if not dir_path.is_dir():
        return []
    definitions: list[Definition] = []
    for path in sorted(dir_path.glob("*.md"), key=lambda item: item.name):
        try:
            definitions.append(parse_file(path, source))
        except Exception as exc:
            print(f"agent {path.name}: {exc}, skipped", file=sys.stderr)
    return definitions


def _home_dir() -> Path:
    value = os.environ.get("MONKEYCODE_HOME") or os.environ.get("HOME")
    if value:
        return Path(value)
    return Path.home()
