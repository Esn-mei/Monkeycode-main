from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


INSTRUCTION_FILENAMES = (
    "MONKEYCODE.md",
    ".monkeycode/instructions.md",
)
USER_INSTRUCTION_FILENAME = ".monkeycode/instructions.md"
INCLUDE_PATTERN = re.compile(r"^\s*@include\s+(?:<(?P<angle>[^>]+)>|(?P<plain>\S+))\s*$")


@dataclass(frozen=True)
class InstructionSource:
    path: Path
    scope: str
    priority: int


@dataclass(frozen=True)
class InstructionBundle:
    content: str
    sources: list[InstructionSource] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def load_project_instructions(
    workspace_root: Path,
    user_home: Path | None = None,
    *,
    max_depth: int = 5,
) -> InstructionBundle:
    workspace = workspace_root.resolve()
    home = (user_home or Path.home()).resolve()
    user_base = (home / ".monkeycode").resolve()
    candidates = [
        (workspace / INSTRUCTION_FILENAMES[0], "project", 10, workspace),
        (workspace / INSTRUCTION_FILENAMES[1], "project", 20, workspace),
        (home / USER_INSTRUCTION_FILENAME, "user", 30, user_base),
    ]
    parts: list[str] = []
    sources: list[InstructionSource] = []
    diagnostics: list[str] = []
    visited: set[Path] = set()

    for path, scope, priority, boundary in candidates:
        if not path.exists():
            continue
        rendered = _load_file(
            path,
            scope=scope,
            boundary=boundary,
            visited=visited,
            diagnostics=diagnostics,
            depth=0,
            max_depth=max_depth,
        )
        if rendered.strip():
            sources.append(InstructionSource(path=path.resolve(), scope=scope, priority=priority))
            parts.append(
                "\n".join(
                    [
                        f"<!-- MonkeyCode instructions: {scope} {path.resolve()} -->",
                        rendered.strip(),
                    ]
                )
            )

    return InstructionBundle(content="\n\n".join(parts), sources=sources, diagnostics=diagnostics)


def _load_file(
    path: Path,
    *,
    scope: str,
    boundary: Path,
    visited: set[Path],
    diagnostics: list[str],
    depth: int,
    max_depth: int,
) -> str:
    resolved = path.resolve()
    if not _is_within(resolved, boundary):
        diagnostics.append(f"include outside allowed boundary skipped: {path}")
        return ""
    if resolved in visited:
        diagnostics.append(f"include cycle skipped: {resolved}")
        return ""
    if depth > max_depth:
        diagnostics.append(f"include depth limit reached: {resolved}")
        return ""
    if resolved.suffix.lower() not in {".md", ".markdown"}:
        diagnostics.append(f"include non-markdown file skipped: {resolved}")
        return ""
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        diagnostics.append(f"include read failed: {resolved}: {exc.__class__.__name__}: {exc}")
        return ""

    visited.add(resolved)
    rendered_lines: list[str] = []
    for line in text.splitlines():
        match = INCLUDE_PATTERN.match(line)
        if not match:
            rendered_lines.append(line)
            continue
        include_target = (match.group("angle") or match.group("plain") or "").strip()
        include_path = (resolved.parent / include_target).resolve()
        rendered = _load_file(
            include_path,
            scope=scope,
            boundary=boundary,
            visited=visited,
            diagnostics=diagnostics,
            depth=depth + 1,
            max_depth=max_depth,
        )
        if rendered:
            rendered_lines.append(rendered)
    visited.remove(resolved)
    return "\n".join(rendered_lines)


def _is_within(path: Path, root: Path) -> bool:
    root = root.resolve()
    return path == root or root in path.parents
