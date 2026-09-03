from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from monkeycode.permissions import PermissionMode
from monkeycode.subagent.definition import Definition, Source

UTF8_BOM = b"\xef\xbb\xbf"
ZERO_WIDTH_SPACE = "\u200b"
AGENT_NAME_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9\-_]{0,31}$")


def parse_frontmatter_and_body(data: bytes | str) -> tuple[dict[str, Any], str]:
    if isinstance(data, bytes):
        if data.startswith(UTF8_BOM):
            data = data[len(UTF8_BOM) :]
        text = data.decode("utf-8")
    else:
        text = data.lstrip("\ufeff")
    text = text.replace(ZERO_WIDTH_SPACE, "")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError("agent definition must start with YAML frontmatter")
    end = normalized.find("\n---\n", 4)
    if end == -1:
        raise ValueError("agent definition frontmatter must be closed by ---")
    frontmatter = normalized[4:end]
    body = normalized[end + len("\n---\n") :]
    if body.startswith("\n"):
        body = body[1:]
    parsed = yaml.safe_load(frontmatter) or {}
    if not isinstance(parsed, dict):
        raise ValueError("agent definition frontmatter must be a mapping")
    return parsed, body


def parse_definition(data: bytes | str, file_path: str, source: Source) -> Definition:
    fm, body = parse_frontmatter_and_body(data)
    name = str(fm.get("name", "")).strip()
    description = str(fm.get("description", "")).strip()
    if not name or not AGENT_NAME_REGEX.fullmatch(name):
        raise ValueError(f"invalid agent name: {name!r}")
    if not description:
        raise ValueError(f"agent {name}: description is required")

    model = str(fm.get("model") or "inherit").strip()
    if not model:
        model = "inherit"

    permission_raw = str(fm.get("permissionMode") or "").strip()
    dont_ask = permission_raw == "dontAsk"
    permission_mode = None
    if permission_raw and not dont_ask:
        try:
            permission_mode = PermissionMode(permission_raw)
        except ValueError:
            permission_mode = PermissionMode.DEFAULT
            print(
                f'agent {name}: unknown permissionMode "{permission_raw}", defaulting to default',
                file=sys.stderr,
            )

    isolation = str(fm.get("isolation") or "none").strip()
    if isolation not in {"none", "worktree"}:
        print(
            f'agent {name}: unknown isolation "{isolation}", defaulting to none',
            file=sys.stderr,
        )
        isolation = "none"

    return Definition(
        name=name,
        description=description,
        tools=_str_list(fm.get("tools")),
        disallowed_tools=_str_list(fm.get("disallowedTools")),
        model=model,
        max_turns=_int_or_zero(fm.get("maxTurns")),
        permission_mode=permission_mode,
        dont_ask=dont_ask,
        background=bool(fm.get("background") or False),
        isolation=isolation,
        system_prompt=body,
        file_path=file_path,
        source=source,
    )


def parse_file(path: str | Path, source: Source) -> Definition:
    file_path = Path(path)
    return parse_definition(file_path.read_bytes(), str(file_path), source)


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _int_or_zero(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)
