from __future__ import annotations

from monkeycode.skills.catalog import Catalog
from monkeycode.skills.parser import parse_skill_dir
from monkeycode.skills.types import SkillSource
from monkeycode.tools.registry import ToolRegistry


def write_skill(
    directory, name: str, description: str, allowed_tools: list[str] | None = None
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    allowed = ""
    if allowed_tools is not None:
        allowed = "\nallowed_tools:\n" + "\n".join(
            f"  - {tool}" for tool in allowed_tools
        )
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}{allowed}\n---\nBody\n",
        encoding="utf-8",
    )


def test_load_catalog_builtin_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    catalog = Catalog.load(tmp_path / "work")

    assert catalog.names() == ["commit", "review", "test"]


def test_load_catalog_user_override(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    write_skill(home / ".monkeycode" / "skills" / "commit", "commit", "User commit")

    catalog = Catalog.load(tmp_path / "work")

    assert catalog.get("commit").meta.description == "User commit"


def test_load_catalog_project_override(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    write_skill(home / ".monkeycode" / "skills" / "commit", "commit", "User commit")
    write_skill(work / ".monkeycode" / "skills" / "commit", "commit", "Project commit")

    catalog = Catalog.load(work)

    assert catalog.get("commit").meta.description == "Project commit"
    assert str(catalog.get("commit").source) == "project"


def test_validate_tools_missing_tool(tmp_path) -> None:
    directory = tmp_path / "foo"
    write_skill(directory, "foo", "Foo skill", ["NotExist"])
    catalog = Catalog()
    catalog.register(parse_skill_dir(directory, SkillSource.PROJECT))

    issues = catalog.validate_tools(ToolRegistry())

    assert [(issue.skill_name, issue.tool_name) for issue in issues] == [
        ("foo", "NotExist")
    ]


def test_validate_tools_allows_system_load_skill(tmp_path) -> None:
    directory = tmp_path / "foo"
    write_skill(directory, "foo", "Foo skill", ["load_skill"])
    catalog = Catalog()
    catalog.register(parse_skill_dir(directory, SkillSource.PROJECT))

    assert catalog.validate_tools(ToolRegistry()) == []
