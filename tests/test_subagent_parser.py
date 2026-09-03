from pathlib import Path

import pytest

from monkeycode.permissions import PermissionMode
from monkeycode.subagent.definition import Definition, Source
from monkeycode.subagent.parser import parse_definition, parse_file


def test_definition_fields() -> None:
    fields = set(Definition.__dataclass_fields__)
    assert {
        "name",
        "description",
        "tools",
        "disallowed_tools",
        "model",
        "max_turns",
        "permission_mode",
        "dont_ask",
        "background",
        "system_prompt",
        "file_path",
        "source",
    } <= fields


def test_parse_definition_full_frontmatter() -> None:
    data = b"""---\nname: Explore\ndescription: read only\ntools:\n- read_file\ndisallowedTools:\n- write_file\nmodel: haiku\nmaxTurns: 7\npermissionMode: dontAsk\nbackground: true\n---\n\nbody text\n"""

    definition = parse_definition(data, "agent.md", Source.PROJECT)

    assert definition.name == "Explore"
    assert definition.tools == ["read_file"]
    assert definition.disallowed_tools == ["write_file"]
    assert definition.model == "haiku"
    assert definition.max_turns == 7
    assert definition.dont_ask is True
    assert definition.permission_mode is None
    assert definition.background is True
    assert definition.system_prompt == "body text\n"


def test_parse_definition_accepts_provider_model_id(capsys) -> None:
    definition = parse_definition(
        b"---\nname: deepseek\ndescription: provider model\nmodel: deepseek-v4-flash\n---\nbody\n",
        "deepseek.md",
        Source.USER,
    )

    assert definition.model == "deepseek-v4-flash"
    assert capsys.readouterr().err == ""


def test_parse_definition_preserves_explicit_permission_mode() -> None:
    definition = parse_definition(
        b"---\nname: guarded\ndescription: guarded agent\npermissionMode: default\n---\nbody\n",
        "guarded.md",
        Source.PROJECT,
    )

    assert definition.permission_mode == PermissionMode.DEFAULT


def test_parse_definition_ignores_zero_width_spaces() -> None:
    definition = parse_definition(
        "---\u200b\r\n"
        "name: pasted-agent\u200b\r\n"
        "description: pasted from an editor\u200b\r\n"
        "model: deepseek-v4-flash\u200b\r\n"
        "---\u200b\r\n"
        "body\u200b\r\n",
        "pasted-agent.md",
        Source.USER,
    )

    assert definition.name == "pasted-agent"
    assert definition.model == "deepseek-v4-flash"
    assert definition.system_prompt == "body\n"


def test_parse_definition_accepts_worktree_isolation() -> None:
    definition = parse_definition(
        "---\n"
        "name: isolated\n"
        "description: isolated agent\n"
        "isolation: worktree\n"
        "---\n"
        "body\n",
        "isolated.md",
        Source.PROJECT,
    )

    assert definition.isolation == "worktree"


def test_parse_definition_invalid_isolation_falls_back(capsys) -> None:
    definition = parse_definition(
        "---\n"
        "name: isolated\n"
        "description: isolated agent\n"
        "isolation: container\n"
        "---\n"
        "body\n",
        "isolated.md",
        Source.PROJECT,
    )

    assert definition.isolation == "none"
    assert "unknown isolation" in capsys.readouterr().err


@pytest.mark.parametrize(
    "body,match",
    [
        (b"---\ndescription: missing name\n---\nbody\n", "invalid agent name"),
        (b"---\nname: ok\n---\nbody\n", "description"),
        (b"---\nname: ok\ndescription: nope\nbody\n", "closed"),
    ],
)
def test_parse_definition_errors(body: bytes, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_definition(body, "bad.md", Source.USER)


def test_parse_file(tmp_path: Path) -> None:
    path = tmp_path / "agent.md"
    path.write_text("---\nname: tester\ndescription: demo\n---\nhello\n", encoding="utf-8")

    assert parse_file(path, Source.USER).system_prompt == "hello\n"
