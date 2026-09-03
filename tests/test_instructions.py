from pathlib import Path

from monkeycode.instructions import load_project_instructions


def test_loads_instruction_layers_in_priority_order(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home_monkey = home / ".monkeycode"
    home_monkey.mkdir(parents=True)
    (workspace / "MONKEYCODE.md").write_text("root project", encoding="utf-8")
    (workspace / ".monkeycode").mkdir()
    (workspace / ".monkeycode" / "instructions.md").write_text("nested project", encoding="utf-8")
    (home_monkey / "instructions.md").write_text("user instruction", encoding="utf-8")

    bundle = load_project_instructions(workspace, home)

    assert bundle.content.index("root project") < bundle.content.index("nested project")
    assert bundle.content.index("nested project") < bundle.content.index("user instruction")
    assert [source.scope for source in bundle.sources] == ["project", "project", "user"]


def test_expands_include(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "details.md").write_text("included content", encoding="utf-8")
    (workspace / "MONKEYCODE.md").write_text("@include <details.md>", encoding="utf-8")

    bundle = load_project_instructions(workspace, tmp_path / "home")

    assert "included content" in bundle.content
    assert bundle.diagnostics == []


def test_include_cycle_is_reported(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MONKEYCODE.md").write_text("@include a.md", encoding="utf-8")
    (workspace / "a.md").write_text("@include MONKEYCODE.md", encoding="utf-8")

    bundle = load_project_instructions(workspace, tmp_path / "home")

    assert any("cycle" in diagnostic for diagnostic in bundle.diagnostics)


def test_include_depth_limit_is_reported(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MONKEYCODE.md").write_text("@include a.md", encoding="utf-8")
    (workspace / "a.md").write_text("@include b.md", encoding="utf-8")
    (workspace / "b.md").write_text("too deep", encoding="utf-8")

    bundle = load_project_instructions(workspace, tmp_path / "home", max_depth=1)

    assert "too deep" not in bundle.content
    assert any("depth" in diagnostic for diagnostic in bundle.diagnostics)


def test_outside_include_is_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text("secret content", encoding="utf-8")
    (workspace / "MONKEYCODE.md").write_text("@include ../secret.md", encoding="utf-8")

    bundle = load_project_instructions(workspace, tmp_path / "home")

    assert "secret content" not in bundle.content
    assert any("outside" in diagnostic for diagnostic in bundle.diagnostics)
