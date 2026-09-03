from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path

from monkeycode.config import WorktreeConfig
from monkeycode.worktree.git import GitRunner
from monkeycode.worktree.initializer import WorktreeInitializer
from monkeycode.worktree.models import (
    STATE_VERSION,
    CleanupOutcome,
    WorktreeLease,
    WorktreeState,
)
from monkeycode.worktree.names import _is_within, managed_path, validate_worktree_name


class WorktreeError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(
        self,
        repository_root: Path,
        config: WorktreeConfig | None = None,
        *,
        git: GitRunner | None = None,
        clock=time.time,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.config = config or WorktreeConfig()
        self.root = (self.repository_root / self.config.root).resolve()
        self.state_root = (self.repository_root / ".monkeycode" / "worktree-state").resolve()
        if not _is_within(self.root, self.repository_root):
            raise WorktreeError("worktree root escapes repository")
        git_root = (self.repository_root / ".git").resolve()
        if self.root == self.repository_root or _is_within(self.root, git_root):
            raise WorktreeError("worktree root overlaps repository metadata")
        if _is_within(self.root, self.state_root) or _is_within(self.state_root, self.root):
            raise WorktreeError("worktree root overlaps managed state")
        self.git = git or GitRunner()
        self.clock = clock
        self.initializer = WorktreeInitializer(self.repository_root, self.config, self.git)
        self._lock = threading.RLock()
        self._name_locks: dict[str, threading.Lock] = {}
        self._active: set[str] = set()

    def acquire(self, name: str) -> WorktreeLease:
        if not self.config.enabled:
            raise WorktreeError("worktree isolation is disabled")
        safe_name = validate_worktree_name(name)
        with self._lock:
            name_lock = self._name_locks.setdefault(safe_name, threading.Lock())
        with name_lock:
            with self._lock:
                if safe_name in self._active:
                    raise WorktreeError(f"worktree is already active: {safe_name}")
            path = managed_path(self.root, safe_name)
            if path.exists():
                state = self._load_and_validate_state(safe_name, path)
                recovered = True
            else:
                state = self._create(safe_name, path)
                recovered = False
            with self._lock:
                self._active.add(safe_name)
            return WorktreeLease(self, state, recovered=recovered)

    def release(self, lease: WorktreeLease) -> CleanupOutcome:
        state = lease.state
        try:
            outcome = self._cleanup_state(state)
        finally:
            with self._lock:
                self._active.discard(state.name)
        if outcome.retained:
            self._touch_state(state)
        return outcome

    def cleanup_expired(self) -> list[CleanupOutcome]:
        outcomes: list[CleanupOutcome] = []
        cutoff = self.clock() - self.config.ttl_hours * 3600
        if not self.state_root.exists():
            return outcomes
        for state_path in self.state_root.rglob("*.json"):
            try:
                state = WorktreeState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))
                if state.last_used_at >= cutoff:
                    continue
                safe_name = validate_worktree_name(state.name)
                with self._lock:
                    name_lock = self._name_locks.setdefault(safe_name, threading.Lock())
                with name_lock:
                    with self._lock:
                        if safe_name in self._active:
                            continue
                    path = managed_path(self.root, safe_name)
                    self._validate_state(state, safe_name, path)
                    outcomes.append(self._cleanup_state(state))
            except Exception:
                continue
        return outcomes

    def _create(self, name: str, path: Path) -> WorktreeState:
        self.root.mkdir(parents=True, exist_ok=True)
        branch = f"monkeycode/worktree/{name}"
        base = self.git.head(self.repository_root)
        now = self.clock()
        state = WorktreeState(
            name=name,
            repository_root=str(self.repository_root),
            path=str(path),
            branch=branch,
            base_commit=base,
            created_at=now,
            last_used_at=now,
        )
        created = False
        try:
            self.git.add_worktree(self.repository_root, path, branch)
            created = True
            self._write_state(state)
            self.initializer.initialize(path)
            return state
        except Exception:
            if created:
                try:
                    self.git.remove_worktree(self.repository_root, path)
                except Exception:
                    pass
                try:
                    self.git.delete_branch(self.repository_root, branch)
                except Exception:
                    pass
            self._state_path(name).unlink(missing_ok=True)
            raise

    def _cleanup_state(self, state: WorktreeState) -> CleanupOutcome:
        path = managed_path(self.root, state.name)
        try:
            self._validate_state(state, state.name, path)
            if self.git.status(path):
                return self._retained(state, "uncommitted or untracked changes")
            commits = self.git.commits_since(path, state.base_commit)
            if commits:
                remote_refs = self.git.remote_refs(self.repository_root)
                if not remote_refs:
                    return self._retained(state, "new commits exist and repository has no remote")
                for commit in commits:
                    if not self.git.remote_refs_containing(self.repository_root, commit):
                        return self._retained(state, f"commit {commit[:12]} is not pushed")
            self.git.remove_worktree(self.repository_root, path)
            self.git.delete_branch(self.repository_root, state.branch)
            self._state_path(state.name).unlink(missing_ok=True)
            return CleanupOutcome(True, False, "clean and fully pushed", str(path), state.branch)
        except Exception as exc:
            return self._retained(state, f"cleanup safety check failed: {exc}")

    def _retained(self, state: WorktreeState, reason: str) -> CleanupOutcome:
        return CleanupOutcome(False, True, reason, state.path, state.branch)

    def _state_path(self, name: str) -> Path:
        relative_name = Path(*validate_worktree_name(name).split("/"))
        relative = relative_name.parent / f"{relative_name.name}.json"
        target = (self.state_root / relative).resolve()
        if not _is_within(target, self.state_root):
            raise WorktreeError("state path escapes managed state root")
        return target

    def _write_state(self, state: WorktreeState) -> None:
        path = self._state_path(state.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _touch_state(self, state: WorktreeState) -> None:
        updated = WorktreeState(
            **{**state.to_dict(), "last_used_at": self.clock()}
        )
        self._write_state(updated)

    def _load_and_validate_state(self, name: str, path: Path) -> WorktreeState:
        state_path = self._state_path(name)
        if not state_path.is_file():
            raise WorktreeError(f"existing directory is not a managed worktree: {path}")
        try:
            state = WorktreeState.from_dict(json.loads(state_path.read_text(encoding="utf-8")))
        except Exception as exc:
            raise WorktreeError(f"invalid worktree metadata: {state_path}") from exc
        self._validate_state(state, name, path)
        return state

    def _validate_state(self, state: WorktreeState, name: str, path: Path) -> None:
        if state.version != STATE_VERSION:
            raise WorktreeError("unsupported worktree metadata version")
        if state.name != name:
            raise WorktreeError("worktree metadata name mismatch")
        if Path(state.repository_root).resolve() != self.repository_root:
            raise WorktreeError("worktree metadata repository mismatch")
        if Path(state.path).resolve() != path.resolve():
            raise WorktreeError("worktree metadata path mismatch")
        if state.branch != f"monkeycode/worktree/{name}":
            raise WorktreeError("worktree metadata branch mismatch")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", state.base_commit):
            raise WorktreeError("worktree metadata base commit is invalid")
        if (
            not math.isfinite(state.created_at)
            or not math.isfinite(state.last_used_at)
            or state.created_at < 0
            or state.last_used_at < state.created_at
        ):
            raise WorktreeError("worktree metadata timestamps are invalid")


class WorktreeCleaner:
    def __init__(self, manager: WorktreeManager, *, interval_seconds: float = 3600) -> None:
        self.manager = manager
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="worktree-cleaner")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.manager.cleanup_expired()
