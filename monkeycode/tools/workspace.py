from __future__ import annotations

from pathlib import Path


class WorkspaceError(ValueError):
    pass


class WorkspaceGuard:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def resolve(self, path: str | Path) -> Path:
        raw_path = Path(path)
        candidate = raw_path if raw_path.is_absolute() else self.workspace_root / raw_path
        resolved = candidate.resolve()
        if resolved != self.workspace_root and self.workspace_root not in resolved.parents:
            raise WorkspaceError(f"path is outside workspace: {path}")
        return resolved

    def resolve_writable(self, path: str | Path) -> Path:
        """Resolve a write target without allowing a symlinked parent to escape."""
        raw_path = Path(path)
        candidate = raw_path if raw_path.is_absolute() else self.workspace_root / raw_path
        if candidate.exists():
            return self.resolve(candidate)
        parent = self.resolve(candidate.parent)
        return parent / candidate.name

    def relative(self, path: str | Path) -> str:
        return self.resolve(path).relative_to(self.workspace_root).as_posix()
