from __future__ import annotations

import os
import re
from pathlib import Path


SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
MAX_NAME_LENGTH = 96


class WorktreeNameError(ValueError):
    pass


def validate_worktree_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise WorktreeNameError("worktree name must contain 1-96 characters")
    if "\\" in name or name.startswith("/") or name.endswith("/"):
        raise WorktreeNameError("worktree name must be a relative POSIX path")
    if re.match(r"^[A-Za-z]:", name) or any(ord(char) < 32 for char in name):
        raise WorktreeNameError("worktree name contains an unsafe path prefix or control character")
    segments = name.split("/")
    if any(segment in {"", ".", ".."} or not SEGMENT_RE.fullmatch(segment) for segment in segments):
        raise WorktreeNameError("worktree name contains an invalid path segment")
    return "/".join(segments)


def managed_path(root: Path, name: str) -> Path:
    safe_name = validate_worktree_name(name)
    base = root.resolve()
    target = (base / Path(*safe_name.split("/"))).resolve()
    if not _is_within(target, base):
        raise WorktreeNameError("worktree path escapes the managed root")
    return target


def _is_within(path: Path, root: Path) -> bool:
    path_text = os.path.normcase(str(path.resolve()))
    root_text = os.path.normcase(str(root.resolve()))
    try:
        return os.path.commonpath([path_text, root_text]) == root_text
    except ValueError:
        return False
