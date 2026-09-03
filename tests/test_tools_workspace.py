from __future__ import annotations

from pathlib import Path

import pytest

from monkeycode.tools.workspace import WorkspaceError, WorkspaceGuard


def test_resolve_writable_rejects_new_file_through_outside_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "workspace-guard-outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "linked-outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink is not available in this environment: {exc}")

    with pytest.raises(WorkspaceError, match="outside workspace"):
        WorkspaceGuard(tmp_path).resolve_writable("linked-outside/new.txt")

    assert not (outside / "new.txt").exists()


def test_resolve_writable_accepts_new_file_in_real_workspace_directory(tmp_path: Path) -> None:
    target = WorkspaceGuard(tmp_path).resolve_writable("notes/new.txt")

    assert target == tmp_path / "notes" / "new.txt"
