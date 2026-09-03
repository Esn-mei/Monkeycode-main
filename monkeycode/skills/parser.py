from __future__ import annotations

import json
import re
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from monkeycode.skills.types import Skill, SkillMeta, SkillSource, ToolSpec

SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
VALID_MODES = {"", "inline", "fork"}
VALID_FORK_CONTEXTS = {"", "none", "recent", "full"}


def validate_skill_name(name: str) -> None:
    if (
        not isinstance(name, str)
        or not (1 <= len(name) <= 32)
        or not SKILL_NAME_RE.fullmatch(name)
    ):
        raise ValueError(f"invalid skill name: {name!r}")


def parse_skill_dir(dir_path: Path, source: SkillSource) -> Skill:
    skill_file = dir_path / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"no SKILL.md in {dir_path}")

    meta_dict, body = parse_frontmatter_and_body(skill_file.read_text(encoding="utf-8"))
    meta = _parse_meta(meta_dict, dir_path)
    tool_specs = (
        parse_tool_json((dir_path / "tool.json").read_bytes(), dir_path)
        if (dir_path / "tool.json").exists()
        else []
    )
    return Skill(
        meta=meta,
        prompt_body=body,
        source_dir=dir_path.resolve(),
        source=source,
        tool_specs=tool_specs,
    )


def parse_frontmatter_and_body(data: str) -> tuple[dict[str, Any], str]:
    normalized = data.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    end = normalized.find("\n---\n", 4)
    if end == -1:
        raise ValueError("SKILL.md frontmatter must be closed by ---")
    frontmatter = normalized[4:end]
    body = normalized[end + len("\n---\n") :]
    parsed = yaml.safe_load(frontmatter) or {}
    if not isinstance(parsed, dict):
        raise ValueError("SKILL.md frontmatter must be a mapping")
    return parsed, body


def parse_tool_json(data: bytes, base_dir: Path) -> list[ToolSpec]:
    try:
        raw = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid tool.json: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ValueError("tool.json must be a JSON object")
    tools = raw.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("tool.json tools must be a list")

    specs: list[ToolSpec] = []
    for index, item in enumerate(tools):
        if not isinstance(item, dict):
            raise ValueError(f"tool.json tools[{index}] must be an object")
        name = item.get("name")
        validate_skill_name(name)
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"tool {name}: description is required")
        input_schema = item.get("input_schema")
        if not isinstance(input_schema, dict):
            raise ValueError(f"tool {name}: input_schema must be an object")
        command = item.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) for part in command)
        ):
            raise ValueError(f"tool {name}: command must be a non-empty string array")
        specs.append(
            ToolSpec(
                name=name,
                description=description,
                input_schema=input_schema,
                command=list(command),
                base_dir=base_dir.resolve(),
            )
        )
    return specs


def read_skill_body(skill: Skill) -> str:
    try:
        _, body = parse_frontmatter_and_body(
            (skill.source_dir / "SKILL.md").read_text(encoding="utf-8")
        )
    except Exception as exc:
        print(
            f"skill {skill.meta.name}: failed to reread SKILL.md, using cached body: {exc}",
            file=sys.stderr,
        )
        return skill.prompt_body
    return body


def _parse_meta(meta_dict: dict[str, Any], dir_path: Path) -> SkillMeta:
    known = {field.name for field in fields(SkillMeta)}
    filtered = {key: value for key, value in meta_dict.items() if key in known}
    missing = [key for key in ("name", "description") if key not in filtered]
    if missing:
        raise ValueError(
            f"{dir_path}: missing required frontmatter field(s): {', '.join(missing)}"
        )

    name = filtered["name"]
    validate_skill_name(name)
    description = filtered["description"]
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"skill {name}: description is required")

    allowed_tools = filtered.get("allowed_tools", [])
    if allowed_tools is None:
        allowed_tools = []
    if not isinstance(allowed_tools, list) or not all(
        isinstance(item, str) for item in allowed_tools
    ):
        raise ValueError(f"skill {name}: allowed_tools must be a string array")

    mode = filtered.get("mode") or "inline"
    if mode not in VALID_MODES:
        print(f"skill {name}: unknown mode {mode!r}, using inline", file=sys.stderr)
        mode = "inline"
    if mode == "":
        mode = "inline"

    fork_context = filtered.get("fork_context") or "none"
    if fork_context not in VALID_FORK_CONTEXTS:
        print(
            f"skill {name}: unknown fork_context {fork_context!r}, using none",
            file=sys.stderr,
        )
        fork_context = "none"
    if fork_context == "":
        fork_context = "none"

    model = filtered.get("model")
    if model is not None and not isinstance(model, str):
        raise ValueError(f"skill {name}: model must be a string")

    return SkillMeta(
        name=name,
        description=description.strip(),
        allowed_tools=list(allowed_tools),
        mode=mode,
        fork_context=fork_context,
        model=model,
    )
