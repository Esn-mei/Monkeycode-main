from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monkeycode.messages import ChatMessage, ToolCall
from monkeycode.plan import DefaultPlanManager, PlanDocument


SESSION_ID_FORMAT = "%Y%m%d-%H%M%S"


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    title: str
    message_count: int
    updated_at: str | None
    last_activity_timestamp: float | None


@dataclass(frozen=True)
class RestoreResult:
    session_id: str
    messages: list[ChatMessage]
    diagnostics: list[str] = field(default_factory=list)
    skipped_bad_lines: int = 0
    truncated_incomplete_tail: bool = False
    restore_notice_added: bool = False
    plan: PlanDocument | None = None


class SessionArchive:
    def __init__(self, workspace_root: Path, session_id: str, path: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.session_id = session_id
        self.path = path

    @classmethod
    def create(cls, workspace_root: Path, *, now: datetime | None = None) -> SessionArchive:
        root = workspace_root.resolve()
        session_id = generate_session_id(now=now)
        path = root / ".monkeycode" / "sessions" / f"{session_id}.jsonl"
        archive = cls(root, session_id, path)
        archive.append("session_started", {}, timestamp=now)
        return archive

    @classmethod
    def open(cls, workspace_root: Path, session_id: str) -> SessionArchive:
        root = workspace_root.resolve()
        return cls(root, session_id, root / ".monkeycode" / "sessions" / f"{session_id}.jsonl")

    def append(self, event_type: str, payload: dict[str, Any], *, timestamp: datetime | None = None) -> None:
        event = {
            "type": event_type,
            "timestamp": _iso(timestamp),
            "session_id": self.session_id,
            "payload": payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def append_user_message(self, content: str, *, timestamp: datetime | None = None) -> None:
        self.append("user_message", {"content": content}, timestamp=timestamp)

    def append_assistant_message(self, content: str, *, timestamp: datetime | None = None) -> None:
        self.append("assistant_message", {"content": content}, timestamp=timestamp)

    def append_assistant_tool_calls(
        self,
        tool_calls: list[ToolCall],
        *,
        content: str = "",
        provider_payload: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        self.append(
            "assistant_tool_calls",
            {
                "content": content,
                "tool_calls": [asdict(call) for call in tool_calls],
                "provider_payload": provider_payload,
            },
            timestamp=timestamp,
        )

    def append_tool_result(self, tool_call_id: str, content: str, *, timestamp: datetime | None = None) -> None:
        self.append("tool_result", {"tool_call_id": tool_call_id, "content": content}, timestamp=timestamp)

    def append_restore_notice(self, content: str, *, timestamp: datetime | None = None) -> None:
        self.append("restore_notice", {"content": content}, timestamp=timestamp)

    def append_plan_event(
        self,
        event_type: str,
        plan: PlanDocument,
        *,
        failure: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        if event_type not in {"plan_created", "plan_checkpoint", "plan_replanned"}:
            raise ValueError(f"unsupported plan event type: {event_type}")
        payload: dict[str, Any] = {"plan": plan.to_dict()}
        if failure:
            payload["failure"] = failure
        self.append(event_type, payload, timestamp=timestamp)

    def append_plan_created(
        self,
        plan: PlanDocument,
        *,
        timestamp: datetime | None = None,
    ) -> None:
        self.append_plan_event("plan_created", plan, timestamp=timestamp)

    def append_plan_checkpoint(
        self,
        plan: PlanDocument,
        *,
        timestamp: datetime | None = None,
    ) -> None:
        self.append_plan_event("plan_checkpoint", plan, timestamp=timestamp)

    def append_plan_replanned(
        self,
        plan: PlanDocument,
        *,
        failure: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        self.append_plan_event("plan_replanned", plan, failure=failure, timestamp=timestamp)

    def end(self, *, timestamp: datetime | None = None) -> None:
        self.append("session_ended", {}, timestamp=timestamp)

    def restore(
        self,
        *,
        stale_after_seconds: int = 24 * 60 * 60,
        now: datetime | None = None,
    ) -> RestoreResult:
        events, bad_lines, diagnostics = _read_events(self.path)
        messages = _events_to_messages(events)
        plan = DefaultPlanManager().recover(events)
        truncated = _truncate_incomplete_tail(messages)
        if truncated < len(messages):
            messages = messages[:truncated]
            diagnostics.append("truncated incomplete trailing tool call")
            was_truncated = True
        else:
            was_truncated = False
        notice_added = False
        last_activity = _last_activity(events)
        current = now or datetime.now(timezone.utc)
        if last_activity is not None and current.timestamp() - last_activity > stale_after_seconds:
            days = int((current.timestamp() - last_activity) // 86400)
            notice = f"会话已中断约 {days} 天。请基于已恢复历史继续，并在需要细节时重新检查文件。"
            messages.append(ChatMessage(role="user", content=notice))
            self.append_restore_notice(notice)
            notice_added = True
        return RestoreResult(
            session_id=self.session_id,
            messages=messages,
            diagnostics=diagnostics,
            skipped_bad_lines=bad_lines,
            truncated_incomplete_tail=was_truncated,
            restore_notice_added=notice_added,
            plan=plan,
        )

    def summarize(self) -> SessionSummary:
        events, _, _ = _read_events(self.path)
        messages = _events_to_messages(events)
        title = ""
        for message in messages:
            if message.role == "user":
                title = str(message.content).strip().splitlines()[0][:80]
                break
        updated_at = events[-1].get("timestamp") if events else None
        return SessionSummary(
            session_id=self.session_id,
            title=title or self.session_id,
            message_count=len(messages),
            updated_at=updated_at if isinstance(updated_at, str) else None,
            last_activity_timestamp=_last_activity(events),
        )

    @staticmethod
    def list_summaries(workspace_root: Path) -> list[SessionSummary]:
        sessions_dir = workspace_root.resolve() / ".monkeycode" / "sessions"
        if not sessions_dir.exists():
            return []
        summaries: list[SessionSummary] = []
        for path in sorted(sessions_dir.glob("*.jsonl")):
            summary = SessionArchive(workspace_root.resolve(), path.stem, path).summarize()
            summaries.append(summary)
        return sorted(
            summaries,
            key=lambda summary: summary.last_activity_timestamp or 0,
            reverse=True,
        )


def generate_session_id(*, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return f"{current.strftime(SESSION_ID_FORMAT)}-{uuid.uuid4().hex[:4]}"


def cleanup_expired_sessions(workspace_root: Path, *, older_than_days: int = 30, now: datetime | None = None) -> int:
    sessions_dir = workspace_root.resolve() / ".monkeycode" / "sessions"
    if not sessions_dir.exists():
        return 0
    current = now or datetime.now(timezone.utc)
    cutoff = current.timestamp() - older_than_days * 86400
    removed = 0
    for path in sessions_dir.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _read_events(path: Path) -> tuple[list[dict[str, Any]], int, list[str]]:
    events: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    bad_lines = 0
    if not path.exists():
        diagnostics.append(f"session archive not found: {path}")
        return events, bad_lines, diagnostics
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            diagnostics.append(f"bad JSONL line skipped: {line_number}")
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events, bad_lines, diagnostics


def _events_to_messages(events: list[dict[str, Any]]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_type = event.get("type")
        if event_type == "user_message":
            messages.append(ChatMessage(role="user", content=str(payload.get("content", ""))))
        elif event_type == "assistant_message":
            messages.append(ChatMessage(role="assistant", content=str(payload.get("content", ""))))
        elif event_type == "assistant_tool_calls":
            calls = [_tool_call(raw) for raw in payload.get("tool_calls", []) if isinstance(raw, dict)]
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=str(payload.get("content", "")),
                    tool_calls=calls,
                    provider_payload=payload.get("provider_payload"),
                )
            )
        elif event_type == "tool_result":
            messages.append(
                ChatMessage(
                    role="tool",
                    content=str(payload.get("content", "")),
                    tool_call_id=str(payload.get("tool_call_id", "")),
                )
            )
        elif event_type == "restore_notice":
            messages.append(ChatMessage(role="user", content=str(payload.get("content", ""))))
    return messages


def _tool_call(raw: dict[str, Any]) -> ToolCall:
    arguments = raw.get("arguments")
    return ToolCall(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        arguments_json=str(raw.get("arguments_json", "{}")),
        arguments=arguments if isinstance(arguments, dict) else None,
        provider=str(raw.get("provider", "")),
    )


def _truncate_incomplete_tail(messages: list[ChatMessage]) -> int:
    pending: set[str] = set()
    tool_call_message_index: int | None = None
    for index, message in enumerate(messages):
        if message.role == "assistant" and message.tool_calls:
            pending = {call.id for call in message.tool_calls}
            tool_call_message_index = index
            continue
        if message.role == "tool" and message.tool_call_id in pending:
            pending.remove(message.tool_call_id or "")
            if not pending:
                tool_call_message_index = None
    if pending and tool_call_message_index is not None:
        return tool_call_message_index
    return len(messages)


def _last_activity(events: list[dict[str, Any]]) -> float | None:
    for event in reversed(events):
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, str):
            continue
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return None


def _iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
