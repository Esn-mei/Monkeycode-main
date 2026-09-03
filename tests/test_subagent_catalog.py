from pathlib import Path

from monkeycode.subagent.catalog import load_catalog
from monkeycode.subagent.definition import Source
from monkeycode.subagent.embed import builtin_definitions


def test_builtin_definitions() -> None:
    names = {definition.name for definition in builtin_definitions()}
    assert names == {"general-purpose", "Explore", "Plan", "security-reviewer"}


def test_catalog_layers_override(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    user_agents = home / ".monkeycode" / "agents"
    project_agents = tmp_path / "project" / ".monkeycode" / "agents"
    user_agents.mkdir(parents=True)
    project_agents.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (user_agents / "explore.md").write_text(
        "---\nname: Explore\ndescription: user\n---\nuser\n",
        encoding="utf-8",
    )
    (project_agents / "explore.md").write_text(
        "---\nname: Explore\ndescription: project\n---\nproject\n",
        encoding="utf-8",
    )

    catalog = load_catalog(tmp_path / "project")

    assert catalog.resolve("Explore").source == Source.PROJECT
    assert catalog.resolve("Explore").system_prompt == "project\n"


def test_catalog_skips_bad_file(tmp_path: Path, capsys) -> None:
    agents = tmp_path / ".monkeycode" / "agents"
    agents.mkdir(parents=True)
    (agents / "bad.md").write_text("---\nname: bad\n---\nbody\n", encoding="utf-8")

    catalog = load_catalog(tmp_path)

    assert catalog.resolve("bad") is None
    assert "skipped" in capsys.readouterr().err


def test_fork_definition() -> None:
    assert load_catalog(".").fork_definition().is_fork() is True
