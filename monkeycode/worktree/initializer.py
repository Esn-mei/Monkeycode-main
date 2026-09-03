from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path

from monkeycode.config import WorktreeConfig
from monkeycode.worktree.git import GitRunner
from monkeycode.worktree.names import _is_within


class InitializationError(RuntimeError):
    pass


class WorktreeInitializer:
    def __init__(self, repository: Path, config: WorktreeConfig, git: GitRunner) -> None:
        self.repository = repository.resolve()
        self.config = config
        self.git = git

    def initialize(self, target: Path) -> None:
        for relative in self.config.copy_paths:
            self._copy_relative(relative, target)
        for pattern in self.config.include_ignored:
            self._copy_glob(pattern, target)
        for relative in self.config.link_dirs:
            self._link_relative(relative, target)
        if self.config.hooks_path:
            hooks = self._source(self.config.hooks_path)
            if not hooks.is_dir():
                raise InitializationError(f"hooks path is not a directory: {self.config.hooks_path}")
            self.git.set_hooks_path(self.repository, target, hooks)

    def _source(self, relative: str) -> Path:
        raw = Path(relative)
        if raw.is_absolute() or ".." in raw.parts:
            raise InitializationError(f"unsafe initialization source: {relative}")
        source = (self.repository / raw).resolve()
        if not _is_within(source, self.repository):
            raise InitializationError(f"initialization source escapes repository: {relative}")
        return source

    def _target(self, target: Path, relative: Path) -> Path:
        destination = (target / relative).resolve()
        if not _is_within(destination, target.resolve()):
            raise InitializationError(f"initialization target escapes worktree: {relative}")
        return destination

    def _copy_relative(self, relative: str, target: Path) -> None:
        source = self._source(relative)
        if not source.exists():
            raise InitializationError(f"copy source does not exist: {relative}")
        destination = self._target(target, Path(relative))
        _copy(source, destination)

    def _copy_glob(self, pattern: str, target: Path) -> None:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise InitializationError(f"unsafe include_ignored pattern: {pattern}")
        for match in glob.glob(str(self.repository / pattern), recursive=True):
            source = Path(match).resolve()
            if not _is_within(source, self.repository):
                raise InitializationError(f"ignored source escapes repository: {source}")
            relative = source.relative_to(self.repository)
            _copy(source, self._target(target, relative))

    def _link_relative(self, relative: str, target: Path) -> None:
        source = self._source(relative)
        if not source.is_dir():
            raise InitializationError(f"link source is not a directory: {relative}")
        destination = self._target(target, Path(relative))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            return
        try:
            os.symlink(source, destination, target_is_directory=True)
            return
        except OSError as exc:
            if os.name != "nt":
                raise InitializationError(f"failed to link dependency {relative}: {exc}") from exc
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(destination), str(source)],
            capture_output=True,
            text=True,
            shell=False,
        )
        if completed.returncode != 0:
            raise InitializationError(
                f"failed to create Junction for {relative}: {completed.stderr.strip()}"
            )


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        raise InitializationError(f"refusing to copy symlink source: {source}")
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
    else:
        shutil.copy2(source, destination)
