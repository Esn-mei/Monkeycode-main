from __future__ import annotations

from pathlib import Path

import pytest

from monkeycode.errors import ConfigError
from monkeycode.permissions import PermissionAction, PermissionRuleStore


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_yaml_rules_and_matches_exact_glob_and_wildcard(tmp_path: Path) -> None:
    local = write(
        tmp_path / ".monkeycode" / "permissions.local.yaml",
        """
rules:
  run_command(git *): allow
  write_file(.env): deny
  edit_file(*): allow
""",
    )

    store = PermissionRuleStore.load(
        tmp_path,
        user_path=tmp_path / "missing-user.yaml",
        project_path=tmp_path / "missing-project.yaml",
        local_path=local,
    )

    git_rule = store.lookup("run_command", "git status")
    env_rule = store.lookup("write_file", ".env")
    edit_rule = store.lookup("edit_file", "src/app.py")

    assert git_rule is not None
    assert git_rule.action == PermissionAction.ALLOW
    assert env_rule is not None
    assert env_rule.action == PermissionAction.DENY
    assert edit_rule is not None
    assert edit_rule.action == PermissionAction.ALLOW


def test_invalid_yaml_reports_config_error_with_path(tmp_path: Path) -> None:
    local = write(tmp_path / ".monkeycode" / "permissions.local.yaml", "rules: [")

    with pytest.raises(ConfigError) as exc_info:
        PermissionRuleStore.load(
            tmp_path,
            user_path=tmp_path / "missing-user.yaml",
            project_path=tmp_path / "missing-project.yaml",
            local_path=local,
        )

    assert str(local) in str(exc_info.value)


def test_invalid_rule_key_reports_config_error(tmp_path: Path) -> None:
    local = write(
        tmp_path / ".monkeycode" / "permissions.local.yaml",
        """
rules:
  run_command git *: allow
""",
    )

    with pytest.raises(ConfigError) as exc_info:
        PermissionRuleStore.load(
            tmp_path,
            user_path=tmp_path / "missing-user.yaml",
            project_path=tmp_path / "missing-project.yaml",
            local_path=local,
        )

    assert "expected tool(pattern)" in str(exc_info.value)


def test_invalid_rule_value_reports_config_error(tmp_path: Path) -> None:
    local = write(
        tmp_path / ".monkeycode" / "permissions.local.yaml",
        """
rules:
  run_command(git *): maybe
""",
    )

    with pytest.raises(ConfigError) as exc_info:
        PermissionRuleStore.load(
            tmp_path,
            user_path=tmp_path / "missing-user.yaml",
            project_path=tmp_path / "missing-project.yaml",
            local_path=local,
        )

    assert "allow or deny" in str(exc_info.value)


def test_rule_priority_local_project_user(tmp_path: Path) -> None:
    user = write(
        tmp_path / "user.yaml",
        """
rules:
  run_command(git *): deny
  write_file(note.txt): deny
""",
    )
    project = write(
        tmp_path / "monkeycode.permissions.yaml",
        """
rules:
  run_command(git *): allow
  write_file(note.txt): allow
""",
    )
    local = write(
        tmp_path / ".monkeycode" / "permissions.local.yaml",
        """
rules:
  write_file(note.txt): deny
""",
    )

    store = PermissionRuleStore.load(
        tmp_path,
        user_path=user,
        project_path=project,
        local_path=local,
    )

    git = store.lookup("run_command", "git status")
    write_rule = store.lookup("write_file", "note.txt")

    assert git is not None
    assert git.action == PermissionAction.ALLOW
    assert git.layer.value == "project"
    assert write_rule is not None
    assert write_rule.action == PermissionAction.DENY
    assert write_rule.layer.value == "local"


def test_session_rules_override_yaml_rules(tmp_path: Path) -> None:
    local = write(
        tmp_path / ".monkeycode" / "permissions.local.yaml",
        """
rules:
  run_command(git *): deny
  write_file(note.txt): allow
""",
    )
    store = PermissionRuleStore.load(
        tmp_path,
        user_path=tmp_path / "missing-user.yaml",
        project_path=tmp_path / "missing-project.yaml",
        local_path=local,
    )
    store.add_session_rule("run_command", "git *", PermissionAction.ALLOW)
    store.add_session_rule("write_file", "note.txt", PermissionAction.DENY)

    git = store.lookup("run_command", "git status")
    write_rule = store.lookup("write_file", "note.txt")

    assert git is not None
    assert git.action == PermissionAction.ALLOW
    assert git.layer.value == "session"
    assert write_rule is not None
    assert write_rule.action == PermissionAction.DENY
    assert write_rule.layer.value == "session"
