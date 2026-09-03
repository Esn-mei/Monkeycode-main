from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from monkeycode.config import WorktreeConfig
from monkeycode.worktree.manager import WorktreeError, WorktreeManager
from monkeycode.worktree.names import WorktreeNameError, managed_path, validate_worktree_name


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "tracked.txt").write_text("base", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "base")
    return root


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "a/../b",
        "a//b",
        "/absolute",
        "C:/drive",
        r"a\b",
        "a/",
        "x" * 97,
        "a/" + "x" * 33,
    ],
)
def test_rejects_unsafe_worktree_names(name: str) -> None:
    with pytest.raises(WorktreeNameError):
        validate_worktree_name(name)


def test_accepts_nested_safe_name(tmp_path: Path) -> None:
    assert validate_worktree_name("feature/api-1") == "feature/api-1"
    root = tmp_path / "managed"
    assert managed_path(root, "feature/api-1") == (root / "feature" / "api-1").resolve()


def test_state_paths_do_not_collide_for_dotted_names(tmp_path: Path) -> None:
    root = repository(tmp_path)
    manager = WorktreeManager(root)

    assert manager._state_path("worker.a") != manager._state_path("worker.b")


@pytest.mark.parametrize("managed_root", [".", ".git/worktrees", ".monkeycode"])
def test_rejects_worktree_roots_that_overlap_repository_state(
    tmp_path: Path,
    managed_root: str,
) -> None:
    root = repository(tmp_path)

    with pytest.raises(WorktreeError, match="overlaps"):
        WorktreeManager(root, WorktreeConfig(root=managed_root))


def test_create_clean_worktree_and_release_removes_it(tmp_path: Path) -> None:
    root = repository(tmp_path)
    manager = WorktreeManager(root)

    lease = manager.acquire("worker/one")

    assert lease.path.is_dir()
    assert (lease.path / "tracked.txt").read_text(encoding="utf-8") == "base"
    assert lease.branch == "monkeycode/worktree/worker/one"
    outcome = lease.release()
    assert outcome.cleaned is True
    assert not lease.path.exists()
    assert "monkeycode/worktree/worker/one" not in git(root, "branch", "--list")


def test_dirty_worktree_is_retained(tmp_path: Path) -> None:
    root = repository(tmp_path)
    manager = WorktreeManager(root)
    lease = manager.acquire("dirty")
    (lease.path / "tracked.txt").write_text("changed", encoding="utf-8")

    outcome = lease.release()

    assert outcome.retained is True
    assert "uncommitted" in outcome.reason
    assert lease.path.exists()


def test_new_commit_without_remote_is_retained(tmp_path: Path) -> None:
    root = repository(tmp_path)
    manager = WorktreeManager(root)
    lease = manager.acquire("committed")
    (lease.path / "tracked.txt").write_text("commit", encoding="utf-8")
    git(lease.path, "add", "tracked.txt")
    git(lease.path, "commit", "-m", "agent")

    outcome = lease.release()

    assert outcome.retained is True
    assert "no remote" in outcome.reason


def test_pushed_commit_allows_worktree_and_temp_branch_cleanup(tmp_path: Path) -> None:
    root = repository(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    manager = WorktreeManager(root)
    lease = manager.acquire("pushed")
    (lease.path / "tracked.txt").write_text("pushed", encoding="utf-8")
    git(lease.path, "add", "tracked.txt")
    git(lease.path, "commit", "-m", "agent")
    git(lease.path, "push", "-u", "origin", lease.branch)

    outcome = lease.release()

    assert outcome.cleaned is True
    assert not lease.path.exists()
    assert lease.branch not in git(root, "branch", "--list")


def test_existing_directory_requires_matching_metadata_without_git(tmp_path: Path) -> None:
    root = repository(tmp_path)
    manager = WorktreeManager(root)
    path = manager.root / "existing"
    path.mkdir(parents=True)

    with pytest.raises(WorktreeError, match="not a managed worktree"):
        manager.acquire("existing")


def test_existing_directory_recovers_from_metadata_without_git(tmp_path: Path) -> None:
    root = repository(tmp_path)
    manager = WorktreeManager(root)
    lease = manager.acquire("recover")
    state = lease.state
    manager._active.clear()

    class NoGit:
        def __getattr__(self, name):
            raise AssertionError(f"Git must not be called during recovery: {name}")

    recovering = WorktreeManager(root, git=NoGit())
    recovered = recovering.acquire("recover")

    assert recovered.recovered is True
    assert recovered.state == state


def test_existing_directory_rejects_tampered_branch_metadata(tmp_path: Path) -> None:
    root = repository(tmp_path)
    manager = WorktreeManager(root)
    lease = manager.acquire("tampered")
    manager._active.clear()
    state_path = manager._state_path("tampered")
    original = json.loads(state_path.read_text(encoding="utf-8"))
    tampered = {**original, "branch": "main"}
    state_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(WorktreeError, match="branch mismatch"):
        manager.acquire("tampered")

    state_path.write_text(json.dumps(original), encoding="utf-8")
    lease.release()


def test_active_worktree_cannot_be_acquired_twice(tmp_path: Path) -> None:
    root = repository(tmp_path)
    manager = WorktreeManager(root)
    lease = manager.acquire("active")

    with pytest.raises(WorktreeError, match="already active"):
        manager.acquire("active")

    lease.release()


def test_explicit_copy_and_ignored_file_initialization(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "local.cfg").write_text("local", encoding="utf-8")
    (root / "runtime.cache").write_text("runtime", encoding="utf-8")
    manager = WorktreeManager(
        root,
        WorktreeConfig(copy_paths=("local.cfg",), include_ignored=("*.cache",)),
    )

    lease = manager.acquire("initialized")

    assert (lease.path / "local.cfg").read_text(encoding="utf-8") == "local"
    assert (lease.path / "runtime.cache").read_text(encoding="utf-8") == "runtime"
    lease.release()


def test_default_initialization_does_not_copy_secret_files(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".gitignore").write_text(".env\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "ignore env")
    (root / ".env").write_text("SECRET=value", encoding="utf-8")
    manager = WorktreeManager(root)

    lease = manager.acquire("no-secrets")

    assert not (lease.path / ".env").exists()
    assert lease.release().cleaned is True


def test_dependency_directory_is_linked_into_worktree(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".gitignore").write_text("vendor/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "ignore dependencies")
    dependency = root / "vendor"
    dependency.mkdir()
    (dependency / "package.txt").write_text("shared", encoding="utf-8")
    manager = WorktreeManager(root, WorktreeConfig(link_dirs=("vendor",)))

    lease = manager.acquire("linked")

    linked = lease.path / "vendor"
    assert linked.is_dir()
    assert (linked / "package.txt").read_text(encoding="utf-8") == "shared"
    assert lease.release().cleaned is True


def test_hooks_path_is_configured_for_only_the_worktree(tmp_path: Path) -> None:
    root = repository(tmp_path)
    hooks = root / ".githooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("# hook", encoding="utf-8")
    git(root, "add", ".githooks")
    git(root, "commit", "-m", "add hooks")
    manager = WorktreeManager(root, WorktreeConfig(hooks_path=".githooks"))

    lease = manager.acquire("hooks")

    configured = git(lease.path, "config", "--worktree", "--get", "core.hooksPath")
    assert Path(configured).resolve() == hooks.resolve()
    assert "core.hookspath=" not in git(root, "config", "--local", "--list").lower()
    assert lease.release().cleaned is True


def test_initialization_failure_rolls_back_worktree_and_branch(tmp_path: Path) -> None:
    root = repository(tmp_path)
    manager = WorktreeManager(root, WorktreeConfig(copy_paths=("missing.cfg",)))

    with pytest.raises(Exception, match="does not exist"):
        manager.acquire("rollback")

    assert not (manager.root / "rollback").exists()
    assert "monkeycode/worktree/rollback" not in git(root, "branch", "--list")


def test_expired_dirty_worktree_is_not_removed(tmp_path: Path) -> None:
    root = repository(tmp_path)
    now = 200_000.0
    manager = WorktreeManager(root, WorktreeConfig(ttl_hours=24), clock=lambda: now)
    lease = manager.acquire("expired")
    (lease.path / "tracked.txt").write_text("dirty", encoding="utf-8")
    manager._active.clear()
    state_path = manager._state_path("expired")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    data["created_at"] = now - 26 * 3600
    data["last_used_at"] = now - 25 * 3600
    state_path.write_text(json.dumps(data), encoding="utf-8")

    outcomes = manager.cleanup_expired()

    assert outcomes[0].retained is True
    assert lease.path.exists()


def test_expired_clean_worktree_is_removed(tmp_path: Path) -> None:
    root = repository(tmp_path)
    now = 200_000.0
    manager = WorktreeManager(root, WorktreeConfig(ttl_hours=24), clock=lambda: now)
    lease = manager.acquire("expired-clean")
    manager._active.clear()
    state_path = manager._state_path("expired-clean")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    data["created_at"] = now - 26 * 3600
    data["last_used_at"] = now - 25 * 3600
    state_path.write_text(json.dumps(data), encoding="utf-8")

    outcomes = manager.cleanup_expired()

    assert outcomes[0].cleaned is True
    assert not lease.path.exists()
    assert not state_path.exists()


def test_expired_active_worktree_is_skipped(tmp_path: Path) -> None:
    root = repository(tmp_path)
    now = 200_000.0
    manager = WorktreeManager(root, WorktreeConfig(ttl_hours=24), clock=lambda: now)
    lease = manager.acquire("still-active")
    state_path = manager._state_path("still-active")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    data["created_at"] = now - 26 * 3600
    data["last_used_at"] = now - 25 * 3600
    state_path.write_text(json.dumps(data), encoding="utf-8")

    assert manager.cleanup_expired() == []
    assert lease.path.exists()
    lease.release()
