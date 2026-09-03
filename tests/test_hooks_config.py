from __future__ import annotations

from pathlib import Path

from monkeycode.hooks.config import load_hook_config


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_load_hook_config_accepts_minimal_rule(tmp_path: Path) -> None:
    project = write(
        tmp_path / "hooks.yaml",
        """
hooks:
  - event: turn.started
    action:
      type: prompt
      content: hello
""",
    )

    config = load_hook_config(tmp_path, user_path=tmp_path / "missing.yaml", project_path=project)

    assert len(config.rules) == 1
    assert config.rules[0].event == "turn.started"
    assert config.issues == []


def test_load_hook_config_skips_invalid_rule_but_keeps_valid_rule(tmp_path: Path) -> None:
    project = write(
        tmp_path / "hooks.yaml",
        """
hooks:
  - action:
      type: prompt
      content: missing event
  - event: turn.started
    action:
      type: prompt
      content: ok
""",
    )

    config = load_hook_config(tmp_path, user_path=tmp_path / "missing.yaml", project_path=project)

    assert [rule.action.params["content"] for rule in config.rules] == ["ok"]
    assert len(config.issues) == 1
    assert "event is required" in config.issues[0].message


def test_load_hook_config_rejects_all_any_mixed_and_bad_regex(tmp_path: Path) -> None:
    project = write(
        tmp_path / "hooks.yaml",
        """
hooks:
  - event: turn.started
    if:
      all: []
      any: []
    action:
      type: prompt
      content: blocked
  - event: turn.started
    if:
      all:
        - field: message.text
          match: regex
          value: "["
    action:
      type: prompt
      content: blocked
""",
    )

    config = load_hook_config(tmp_path, user_path=tmp_path / "missing.yaml", project_path=project)

    assert config.rules == []
    assert len(config.issues) == 2
    assert any("cannot declare both all and any" in issue.message for issue in config.issues)
    assert any("invalid regex" in issue.message for issue in config.issues)


def test_load_hook_config_rejects_async_intercept(tmp_path: Path) -> None:
    project = write(
        tmp_path / "hooks.yaml",
        """
hooks:
  - event: tool.before
    action:
      type: prompt
      target: tool_result
      content: blocked
    control:
      background: true
""",
    )

    config = load_hook_config(tmp_path, user_path=tmp_path / "missing.yaml", project_path=project)

    assert config.rules == []
    assert any("cannot run in background" in issue.message for issue in config.issues)


def test_load_hook_config_requires_subagent_prompt(tmp_path: Path) -> None:
    project = write(
        tmp_path / "hooks.yaml",
        """
hooks:
  - event: tool.error
    action:
      type: subagent
      subagent_type: explore
""",
    )

    config = load_hook_config(tmp_path, user_path=tmp_path / "missing.yaml", project_path=project)

    assert config.rules == []
    assert any("subagent action requires prompt" in issue.message for issue in config.issues)


def test_load_hook_config_validates_subagent_isolation_and_lifecycle(tmp_path: Path) -> None:
    project = write(
        tmp_path / "hooks.yaml",
        """
hooks:
  - event: tool.error
    action:
      type: subagent
      prompt: investigate
      isolation: container
  - event: system.config_loaded
    action:
      type: subagent
      prompt: investigate
  - event: tool.error
    action:
      type: subagent
      prompt: investigate
      subagent_type: explore
      name: hook-investigator
      isolation: worktree
""",
    )

    config = load_hook_config(tmp_path, user_path=tmp_path / "missing.yaml", project_path=project)

    assert len(config.rules) == 1
    assert config.rules[0].action.params["subagent_type"] == "explore"
    assert len(config.issues) == 2
    assert any("isolation must be none or worktree" in issue.message for issue in config.issues)
    assert any("unavailable during system.config_loaded" in issue.message for issue in config.issues)


def test_load_hook_config_preserves_user_project_local_order(tmp_path: Path) -> None:
    user = write(
        tmp_path / "user.yaml",
        "hooks:\n- event: turn.started\n  action:\n    type: prompt\n    content: user\n",
    )
    project = write(
        tmp_path / "project.yaml",
        "hooks:\n- event: turn.started\n  action:\n    type: prompt\n    content: project\n",
    )
    local = write(
        tmp_path / "local.yaml",
        "hooks:\n- event: turn.started\n  action:\n    type: prompt\n    content: local\n",
    )

    config = load_hook_config(tmp_path, user_path=user, project_path=project, local_path=local)

    assert [rule.action.params["content"] for rule in config.rules] == ["user", "project", "local"]
