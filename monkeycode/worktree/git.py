from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class GitRunner:
    def run(self, cwd: Path, *args: str, check: bool = True) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        if check and completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise GitError(f"git {' '.join(args)} failed: {detail}")
        return completed.stdout.strip()

    def repository_root(self, cwd: Path) -> Path:
        return Path(self.run(cwd, "rev-parse", "--show-toplevel")).resolve()

    def head(self, cwd: Path) -> str:
        return self.run(cwd, "rev-parse", "HEAD")

    def add_worktree(self, repository: Path, path: Path, branch: str) -> None:
        self.run(repository, "worktree", "add", "-b", branch, str(path), "HEAD")

    def remove_worktree(self, repository: Path, path: Path) -> None:
        self.run(repository, "worktree", "remove", str(path))

    def delete_branch(self, repository: Path, branch: str) -> None:
        # 调用方已完成未推送提交保护；这里强制删除仅限受管临时分支。
        self.run(repository, "branch", "-D", branch)

    def status(self, worktree: Path) -> str:
        return self.run(worktree, "status", "--porcelain=v1", "--untracked-files=all")

    def commits_since(self, worktree: Path, base: str) -> list[str]:
        output = self.run(worktree, "rev-list", f"{base}..HEAD")
        return [line for line in output.splitlines() if line]

    def remote_refs(self, repository: Path) -> list[str]:
        output = self.run(repository, "for-each-ref", "--format=%(refname)", "refs/remotes")
        return [line for line in output.splitlines() if line]

    def remote_refs_containing(self, repository: Path, commit: str) -> list[str]:
        output = self.run(repository, "branch", "-r", "--contains", commit)
        return [line.strip() for line in output.splitlines() if line.strip()]

    def set_hooks_path(self, repository: Path, worktree: Path, hooks_path: Path) -> None:
        self.run(repository, "config", "extensions.worktreeConfig", "true")
        self.run(worktree, "config", "--worktree", "core.hooksPath", str(hooks_path))
