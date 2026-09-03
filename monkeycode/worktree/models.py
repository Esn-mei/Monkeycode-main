from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


STATE_VERSION = 1


@dataclass(frozen=True)
class WorktreeState:
    name: str
    repository_root: str
    path: str
    branch: str
    base_commit: str
    created_at: float
    last_used_at: float
    version: int = STATE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorktreeState:
        return cls(
            name=str(data["name"]),
            repository_root=str(data["repository_root"]),
            path=str(data["path"]),
            branch=str(data["branch"]),
            base_commit=str(data["base_commit"]),
            created_at=float(data["created_at"]),
            last_used_at=float(data["last_used_at"]),
            version=int(data.get("version", 0)),
        )


@dataclass(frozen=True)
class CleanupOutcome:
    cleaned: bool
    retained: bool
    reason: str
    path: str
    branch: str

    def summary(self) -> str:
        status = "cleaned" if self.cleaned else "retained"
        return f"Worktree {status}: {self.path} ({self.reason})"


@dataclass
class WorktreeLease:
    manager: Any
    state: WorktreeState
    recovered: bool = False
    released: bool = False
    cleanup: CleanupOutcome | None = None

    @property
    def path(self) -> Path:
        return Path(self.state.path)

    @property
    def branch(self) -> str:
        return self.state.branch

    def release(self) -> CleanupOutcome:
        if not self.released:
            self.cleanup = self.manager.release(self)
            self.released = True
        return self.cleanup
