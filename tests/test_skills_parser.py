from __future__ import annotations

import json

import pytest

from monkeycode.skills.parser import parse_skill_dir
from monkeycode.skills.types import SkillSource


def write_skill(directory, frontmatter: str, body: str = "Body") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8"
    )


def test_parse_skill_dir_minimal(tmp_path) -> None:
    write_skill(tmp_path / "demo", "name: demo\ndescription: Demo skill")

    skill = parse_skill_dir(tmp_path / "demo", SkillSource.USER)

    assert skill.meta.name == "demo"
    assert skill.meta.description == "Demo skill"
    assert skill.meta.mode == "inline"
    assert skill.prompt_body == "Body\n"


def test_parse_skill_dir_invalid_name(tmp_path) -> None:
    write_skill(tmp_path / "bad", "name: BadName\ndescription: Demo skill")

    with pytest.raises(ValueError, match="invalid skill name"):
        parse_skill_dir(tmp_path / "bad", SkillSource.USER)


def test_parse_skill_dir_with_tool_json(tmp_path) -> None:
    directory = tmp_path / "demo"
    write_skill(directory, "name: demo\ndescription: Demo skill")
    (directory / "tool.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "parse-resume",
                        "description": "Parse resume.",
                        "input_schema": {"type": "object", "properties": {}},
                        "command": ["parse_resume.py"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    skill = parse_skill_dir(directory, SkillSource.PROJECT)

    assert len(skill.tool_specs) == 1
    assert skill.tool_specs[0].name == "parse-resume"
    assert skill.tool_specs[0].base_dir == directory.resolve()


def test_parse_skill_dir_no_skill_md(tmp_path) -> None:
    (tmp_path / "missing").mkdir()

    with pytest.raises(FileNotFoundError):
        parse_skill_dir(tmp_path / "missing", SkillSource.USER)
