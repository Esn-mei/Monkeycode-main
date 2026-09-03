from __future__ import annotations

import concurrent.futures
import inspect
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

from monkeycode.messages import ChatMessage
from monkeycode.prompting import ProviderPromptPayload


MEMORY_CATEGORIES = {"preference", "correction", "project_knowledge", "reference"}
INDEX_FILENAME = "index.md"
SENSITIVE_PATTERNS = [
    re.compile(r"api[_-]?key\s*[:=]", re.IGNORECASE),
    re.compile(r"authorization\s*:", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
]


@dataclass(frozen=True)
class MemoryNote:
    id: str
    scope: str
    category: str
    content: str
    created_at: str
    updated_at: str
    source_session: str
    confidence: float = 0.5


@dataclass(frozen=True)
class TurnSnapshot:
    session_id: str
    messages: list[ChatMessage]
    existing_index: str = ""
    instructions: str = ""


class MemoryStore:
    def __init__(self, workspace_root: Path, user_home: Path | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self.user_home = (user_home or Path.home()).resolve()
        self.project_root = self.workspace_root / ".monkeycode" / "memory"
        self.user_root = self.user_home / ".monkeycode" / "memory"

    def root_for(self, scope: str) -> Path:
        if scope == "project":
            return self.project_root
        if scope == "user":
            return self.user_root
        raise ValueError(f"unknown memory scope: {scope}")

    def write_note(
        self,
        *,
        scope: str,
        category: str,
        content: str,
        source_session: str,
        note_id: str | None = None,
        confidence: float = 0.5,
    ) -> MemoryNote | None:
        if category not in MEMORY_CATEGORIES:
            raise ValueError(f"unknown memory category: {category}")
        filtered = _filter_sensitive(content)
        if not filtered.strip():
            return None
        current = _now_iso()
        note = MemoryNote(
            id=note_id or uuid.uuid4().hex,
            scope=scope,
            category=category,
            content=filtered.strip(),
            created_at=current,
            updated_at=current,
            source_session=source_session,
            confidence=confidence,
        )
        path = self._note_path(note)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_note(note), encoding="utf-8")
        self.rebuild_index(scope)
        return note

    def update_note(self, *, scope: str, note_id: str, content: str, source_session: str) -> MemoryNote | None:
        existing_path = next(self.root_for(scope).glob(f"*/{note_id}.md"), None)
        if existing_path is None:
            return self.write_note(
                scope=scope,
                category="project_knowledge" if scope == "project" else "preference",
                content=content,
                source_session=source_session,
                note_id=note_id,
            )
        parsed = parse_note(existing_path)
        if parsed is None:
            return None
        updated = MemoryNote(
            id=parsed.id,
            scope=parsed.scope,
            category=parsed.category,
            content=_filter_sensitive(content).strip(),
            created_at=parsed.created_at,
            updated_at=_now_iso(),
            source_session=source_session,
            confidence=parsed.confidence,
        )
        existing_path.write_text(_render_note(updated), encoding="utf-8")
        self.rebuild_index(scope)
        return updated

    def load_index(self, scope: str) -> str:
        path = self.root_for(scope) / INDEX_FILENAME
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def combined_index(self) -> str:
        parts = []
        for scope in ("project", "user"):
            index = self.load_index(scope).strip()
            if index:
                parts.append(f"## {scope} memory\n{index}")
        return "\n\n".join(parts)

    def list_files(self) -> tuple[list[str], list[str]]:
        return (_list_markdown_files(self.project_root), _list_markdown_files(self.user_root))

    def rebuild_index(self, scope: str) -> str:
        root = self.root_for(scope)
        notes = []
        if root.exists():
            for path in sorted(root.glob("*/*.md")):
                note = parse_note(path)
                if note is not None:
                    notes.append((path, note))
        lines = [f"# MonkeyCode {scope} memory index", ""]
        for category in sorted(MEMORY_CATEGORIES):
            category_notes = [(path, note) for path, note in notes if note.category == category]
            if not category_notes:
                continue
            lines.extend([f"## {category}", ""])
            for path, note in category_notes:
                summary = " ".join(note.content.split())[:160]
                rel = path.relative_to(root).as_posix()
                lines.append(f"- `{rel}`: {summary}")
            lines.append("")
        text = _limit_index("\n".join(lines).strip() + "\n")
        root.mkdir(parents=True, exist_ok=True)
        (root / INDEX_FILENAME).write_text(text, encoding="utf-8")
        return text

    def schedule_update(self, provider: Any, snapshot: TurnSnapshot) -> concurrent.futures.Future:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.update_from_provider, provider, snapshot)
        future.add_done_callback(lambda _: executor.shutdown(wait=False))
        return future

    def update_from_provider(self, provider: Any, snapshot: TurnSnapshot) -> list[MemoryNote]:
        text = _collect_provider_text(provider, _memory_prompt(snapshot))
        try:
            operations = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(operations, list):
            return []
        notes: list[MemoryNote] = []
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            action = operation.get("action")
            if action in {"ignore", None}:
                continue
            scope = operation.get("scope")
            category = operation.get("category")
            content = operation.get("content")
            if scope not in {"user", "project"} or category not in MEMORY_CATEGORIES or not isinstance(content, str):
                continue
            if action == "add":
                note = self.write_note(
                    scope=scope,
                    category=category,
                    content=content,
                    source_session=snapshot.session_id,
                    confidence=float(operation.get("confidence", 0.5) or 0.5),
                )
            elif action in {"update", "merge"} and isinstance(operation.get("id"), str):
                note = self.update_note(
                    scope=scope,
                    note_id=operation["id"],
                    content=content,
                    source_session=snapshot.session_id,
                )
            else:
                note = None
            if note is not None:
                notes.append(note)
        return notes

    def _note_path(self, note: MemoryNote) -> Path:
        return self.root_for(note.scope) / note.category / f"{note.id}.md"


def parse_note(path: Path) -> MemoryNote | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    try:
        _, raw_frontmatter, body = text.split("---\n", 2)
    except ValueError:
        return None
    data: dict[str, str] = {}
    for line in raw_frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    category = data.get("category", "")
    scope = data.get("scope", "")
    if category not in MEMORY_CATEGORIES or scope not in {"user", "project"}:
        return None
    return MemoryNote(
        id=data.get("id", path.stem),
        scope=scope,
        category=category,
        content=body.strip(),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        source_session=data.get("source_session", ""),
        confidence=float(data.get("confidence", "0.5") or 0.5),
    )


def _render_note(note: MemoryNote) -> str:
    return "\n".join(
        [
            "---",
            f'id: "{note.id}"',
            f"scope: {note.scope}",
            f"category: {note.category}",
            f"created_at: {note.created_at}",
            f"updated_at: {note.updated_at}",
            f'source_session: "{note.source_session}"',
            f"confidence: {note.confidence}",
            "---",
            "",
            note.content.strip(),
            "",
        ]
    )


def _list_markdown_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    try:
        files = [
            path.name
            for path in root.rglob("*.md")
            if path.is_file()
        ]
    except OSError as exc:
        logging.warning("failed to list memory files in %s: %s", root, exc)
        return []
    return sorted(files)


def _limit_index(text: str) -> str:
    lines = text.splitlines()[:200]
    limited = "\n".join(lines).strip() + "\n"
    while len(limited.encode("utf-8")) > 25_000 and lines:
        lines.pop()
        limited = "\n".join(lines).strip() + "\n"
    return limited


def _filter_sensitive(content: str) -> str:
    text = content
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _collect_provider_text(provider: Any, prompt: str) -> str:
    messages = [ChatMessage(role="user", content=prompt)]
    payload = ProviderPromptPayload(
        stable_system_text=(
            "You update MonkeyCode memory. Return a JSON array of operations only. "
            "Do not call tools."
        )
    )
    events = _stream_provider_without_tools(provider, messages, payload)
    parts: list[str] = []
    for event in events:
        if event.type == "text_delta" and event.text:
            parts.append(event.text)
    return "".join(parts)


def _stream_provider_without_tools(provider: Any, messages: list[ChatMessage], prompt_payload: ProviderPromptPayload):
    signature = inspect.signature(provider.stream_chat)
    parameters = signature.parameters
    accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    accepts_tools = "tools" in parameters or accepts_kwargs or len(positional) >= 2
    args: list[Any] = [messages]
    if accepts_tools:
        args.append(None)
    kwargs: dict[str, Any] = {}
    if "allow_tool_calls" in parameters or accepts_kwargs:
        kwargs["allow_tool_calls"] = False
    if "prompt_payload" in parameters or accepts_kwargs:
        kwargs["prompt_payload"] = prompt_payload
    return provider.stream_chat(*args, **kwargs)


def _memory_prompt(snapshot: TurnSnapshot) -> str:
    messages = [
        {
            "role": message.role,
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
        for message in snapshot.messages
    ]
    return "\n".join(
        [
            "请从本轮 MonkeyCode 对话中提炼长期记忆。",
            "只返回 JSON 数组。每个元素包含 action, scope, category, content，可选 id/confidence。",
            "action 只能是 add/update/merge/ignore。",
            "scope 只能是 user/project。",
            "category 只能是 preference/correction/project_knowledge/reference。",
            "不要保存 API key、认证头、密钥或隐私文件内容。",
            "如果没有值得保存的信息，返回 []。",
            "",
            "已有索引：",
            snapshot.existing_index,
            "",
            "项目指令摘要：",
            snapshot.instructions[:2000],
            "",
            "本轮消息 JSON：",
            json.dumps(messages, ensure_ascii=False),
        ]
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
