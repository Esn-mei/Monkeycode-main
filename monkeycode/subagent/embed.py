from __future__ import annotations

from importlib.resources import files

from monkeycode.subagent.definition import Definition, Source
from monkeycode.subagent.parser import parse_definition


def builtin_definitions() -> list[Definition]:
    pkg = files("monkeycode.subagent.builtin")
    definitions: list[Definition] = []
    for entry in pkg.iterdir():
        if not entry.name.endswith(".md"):
            continue
        definitions.append(
            parse_definition(entry.read_bytes(), f"builtin:{entry.name}", Source.BUILTIN)
        )
    return sorted(definitions, key=lambda item: item.name)
