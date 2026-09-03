from __future__ import annotations

from pathlib import Path

import pytest

from monkeycode.permissions import (
    HumanDecision,
    PermissionAction,
    PermissionManager,
    PermissionMode,
    PermissionRuleStore,
)
from monkeycode.tools.base import ToolPolicy


READ_POLICY = ToolPolicy(
    tool_name="read_file",
    category="read",
    allowed_in_plan_mode=True,
    can_run_parallel=True,
    has_side_effects=False,
)
WRITE_POLICY = ToolPolicy(tool_name="write_file", category="write")
EDIT_POLICY = ToolPolicy(tool_name="edit_file", category="write")
COMMAND_POLICY = ToolPolicy(tool_name="run_command", category="command")


class ScriptedPrompter:
    def __init__(self, *decisions: HumanDecision) -> None:
        self.decisions = list(decisions)
        self.requests = []

    def prompt(self, request, decision):
        self.requests.append((request, decision))
        if not self.decisions:
            return HumanDecision.DENY
        return self.decisions.pop(0)


def manager(
    tmp_path: Path,
    *,
    mode: PermissionMode = PermissionMode.DEFAULT,
    prompter=None,
    store: PermissionRuleStore | None = None,
) -> PermissionManager:
    return PermissionManager(
        mode=mode,
        rule_store=store or PermissionRuleStore.empty(tmp_path),
        prompter=prompter,
    )


def test_default_mode_and_permission_denial_metadata(tmp_path: Path) -> None:
    permission = manager(tmp_path).authorize(
        "write_file",
        {"path": "note.txt", "content": "hello"},
        WRITE_POLICY,
        tmp_path,
    )

    assert permission.allowed is False
    assert permission.denial_result is not None
    assert permission.denial_result.success is False
    assert permission.denial_result.error_type == "permission_denied"
    assert permission.denial_result.metadata["permission_decision"] == "deny"
    assert permission.denial_result.metadata["permission_layer"] == "human"
    assert permission.denial_result.metadata["permission_rule"] is None
    assert permission.denial_result.metadata["permission_mode"] == "default"
    assert permission.denial_result.metadata["permission_target"] == "note.txt"


def test_dangerous_commands_are_hard_denied_before_rules_and_allow_mode(tmp_path: Path) -> None:
    store = PermissionRuleStore.empty(tmp_path)
    store.add_session_rule("run_command", "*", PermissionAction.ALLOW)
    permission = PermissionManager(
        mode=PermissionMode.ALLOW,
        rule_store=store,
    )

    for command in [
        "rm -rf /",
        r"rm -rf C:\\",
        r"Remove-Item -Recurse -Force C:\\Windows",
        "format C:",
        "Clear-Disk -Number 0",
    ]:
        result = permission.authorize(
            "run_command",
            {"command": command},
            COMMAND_POLICY,
            tmp_path,
        )
        assert result.allowed is False
        assert result.denial_result is not None
        assert result.denial_result.error_type == "dangerous_command_denied"
        assert result.denial_result.metadata["permission_layer"] == "blacklist"


def test_normal_command_is_not_blocked_by_blacklist(tmp_path: Path) -> None:
    permission = manager(tmp_path, mode=PermissionMode.ALLOW).authorize(
        "run_command",
        {"command": "git status"},
        COMMAND_POLICY,
        tmp_path,
    )

    assert permission.allowed is True
    assert permission.decision.layer.value == "mode"


def test_path_sandbox_denies_relative_and_absolute_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "secret.txt"
    permission = manager(tmp_path, mode=PermissionMode.ALLOW)

    relative = permission.authorize("read_file", {"path": "../secret.txt"}, READ_POLICY, tmp_path)
    absolute = permission.authorize("write_file", {"path": str(outside), "content": "x"}, WRITE_POLICY, tmp_path)

    assert relative.allowed is False
    assert relative.denial_result is not None
    assert relative.denial_result.error_type == "path_outside_workspace"
    assert relative.denial_result.metadata["permission_layer"] == "sandbox"
    assert absolute.allowed is False
    assert absolute.denial_result is not None
    assert absolute.denial_result.error_type == "path_outside_workspace"


def test_path_sandbox_denies_symlink_escape(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / "outside-permission-target"
    outside_dir.mkdir(exist_ok=True)
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")
    link_file = tmp_path / "link.txt"
    link_dir = tmp_path / "outside"
    try:
        link_file.symlink_to(outside_file)
        link_dir.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink is not available in this environment: {exc}")

    permission = manager(tmp_path, mode=PermissionMode.ALLOW)

    read = permission.authorize("read_file", {"path": "link.txt"}, READ_POLICY, tmp_path)
    edit = permission.authorize(
        "edit_file",
        {"path": "link.txt", "old_text": "secret", "new_text": "x"},
        EDIT_POLICY,
        tmp_path,
    )
    write = permission.authorize(
        "write_file",
        {"path": "outside/new.txt", "content": "x"},
        WRITE_POLICY,
        tmp_path,
    )

    assert read.allowed is False
    assert edit.allowed is False
    assert write.allowed is False
    assert read.denial_result is not None
    assert read.denial_result.metadata["permission_layer"] == "sandbox"


def test_workspace_new_file_passes_sandbox_and_outside_new_file_fails(tmp_path: Path) -> None:
    permission = manager(tmp_path, mode=PermissionMode.ALLOW)

    inside = permission.authorize(
        "write_file",
        {"path": "notes/new.txt", "content": "x"},
        WRITE_POLICY,
        tmp_path,
    )
    outside = permission.authorize(
        "write_file",
        {"path": "../new-secret.txt", "content": "x"},
        WRITE_POLICY,
        tmp_path,
    )

    assert inside.allowed is True
    assert outside.allowed is False
    assert outside.denial_result is not None
    assert outside.denial_result.error_type == "path_outside_workspace"


def test_permission_modes_for_unmatched_tools(tmp_path: Path) -> None:
    default_read = manager(tmp_path).authorize("read_file", {"path": "a.txt"}, READ_POLICY, tmp_path)
    default_write = manager(tmp_path).authorize(
        "write_file",
        {"path": "a.txt", "content": "x"},
        WRITE_POLICY,
        tmp_path,
    )
    strict_command = manager(tmp_path, mode=PermissionMode.STRICT).authorize(
        "run_command",
        {"command": "git status"},
        COMMAND_POLICY,
        tmp_path,
    )
    allow_write = manager(tmp_path, mode=PermissionMode.ALLOW).authorize(
        "write_file",
        {"path": "a.txt", "content": "x"},
        WRITE_POLICY,
        tmp_path,
    )

    assert default_read.allowed is True
    assert default_write.allowed is False
    assert strict_command.allowed is False
    assert allow_write.allowed is True


def test_allow_mode_does_not_bypass_sandbox(tmp_path: Path) -> None:
    result = manager(tmp_path, mode=PermissionMode.ALLOW).authorize(
        "write_file",
        {"path": "../x.txt", "content": "x"},
        WRITE_POLICY,
        tmp_path,
    )

    assert result.allowed is False
    assert result.denial_result is not None
    assert result.denial_result.metadata["permission_layer"] == "sandbox"


def test_session_allow_rule_and_human_allow_cannot_bypass_sandbox(tmp_path: Path) -> None:
    store = PermissionRuleStore.empty(tmp_path)
    store.add_session_rule("write_file", "../secret.txt", PermissionAction.ALLOW)
    prompter = ScriptedPrompter(HumanDecision.ALLOW_ONCE)
    permission = manager(tmp_path, prompter=prompter, store=store)

    result = permission.authorize(
        "write_file",
        {"path": "../secret.txt", "content": "blocked"},
        WRITE_POLICY,
        tmp_path,
    )

    assert result.allowed is False
    assert result.denial_result is not None
    assert result.denial_result.metadata["permission_layer"] == "sandbox"
    assert prompter.requests == []


def test_allow_once_only_affects_current_call(tmp_path: Path) -> None:
    prompter = ScriptedPrompter(HumanDecision.ALLOW_ONCE, HumanDecision.DENY)
    permission = manager(tmp_path, prompter=prompter)

    first = permission.authorize(
        "write_file",
        {"path": "note.txt", "content": "one"},
        WRITE_POLICY,
        tmp_path,
    )
    second = permission.authorize(
        "write_file",
        {"path": "note.txt", "content": "two"},
        WRITE_POLICY,
        tmp_path,
    )

    assert first.allowed is True
    assert second.allowed is False
    assert len(prompter.requests) == 2


def test_session_allow_skips_future_prompt_for_same_target(tmp_path: Path) -> None:
    prompter = ScriptedPrompter(HumanDecision.ALLOW_SESSION)
    permission = manager(tmp_path, prompter=prompter)

    first = permission.authorize(
        "write_file",
        {"path": "note.txt", "content": "one"},
        WRITE_POLICY,
        tmp_path,
    )
    second = permission.authorize(
        "write_file",
        {"path": "note.txt", "content": "two"},
        WRITE_POLICY,
        tmp_path,
    )

    assert first.allowed is True
    assert second.allowed is True
    assert len(prompter.requests) == 1
    assert second.decision.layer.value == "session"


def test_permanent_allow_writes_local_yaml(tmp_path: Path) -> None:
    prompter = ScriptedPrompter(HumanDecision.ALLOW_PERMANENT)
    store = PermissionRuleStore.empty(tmp_path)
    permission = manager(tmp_path, prompter=prompter, store=store)

    result = permission.authorize(
        "write_file",
        {"path": "note.txt", "content": "one"},
        WRITE_POLICY,
        tmp_path,
    )

    assert result.allowed is True
    assert store.local_path is not None
    assert store.local_path.exists()
    loaded = PermissionRuleStore.load(
        tmp_path,
        user_path=tmp_path / "missing-user.yaml",
        project_path=tmp_path / "missing-project.yaml",
        local_path=store.local_path,
    )
    rule = loaded.lookup("write_file", "note.txt")
    assert rule is not None
    assert rule.action == PermissionAction.ALLOW
