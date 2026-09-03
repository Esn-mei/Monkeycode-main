from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from queue import Queue
from typing import Any

from monkeycode.session import ChatSession


class Status(IntEnum):
    RUNNING = 1
    COMPLETED = 2
    FAILED = 3
    CANCELLED = 4

    def __str__(self) -> str:
        return self.name.lower()


@dataclass
class BackgroundTask:
    id: str
    name: str
    runner: Any
    session: ChatSession
    task: str
    status: Status = Status.RUNNING
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    result: str = ""
    err: BaseException | None = None
    tool_count: int = 0
    last_activity: str = ""
    handle: threading.Thread | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    workspace_path: str = ""
    branch: str = ""
    cleanup: str = ""


class TaskBusy(RuntimeError):
    pass


class Manager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._by_name: dict[str, str] = {}
        self._done: Queue[str] = Queue(maxsize=32)

    def get(self, task_id: str) -> BackgroundTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[BackgroundTask]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda item: item.start_time)

    def subscribe_done(self) -> Queue[str]:
        return self._done

    def launch(self, runner: Any, session: ChatSession, name: str, task_text: str) -> str:
        task_id = self._next_id()
        bt = BackgroundTask(id=task_id, name=name, runner=runner, session=session, task=task_text)
        self._register(bt)
        bt.handle = threading.Thread(target=self._run_task, args=(bt, task_text), daemon=True)
        bt.handle.start()
        return task_id

    def stop(self, task_id: str) -> bool:
        bt = self.get(task_id)
        if bt is None:
            return False
        bt.cancel_event.set()
        bt.status = Status.CANCELLED
        bt.end_time = time.monotonic()
        self._notify_done(bt.id)
        return True

    def send_message(self, name: str, message: str) -> str:
        with self._lock:
            task_id = self._by_name.get(name)
        if task_id is None:
            raise KeyError(name)
        bt = self.get(task_id)
        if bt is None:
            raise KeyError(name)
        if bt.status == Status.RUNNING:
            raise TaskBusy(f"task {name} is still running")
        bt.status = Status.RUNNING
        bt.err = None
        bt.result = ""
        bt.task = message
        bt.cancel_event = threading.Event()
        bt.handle = threading.Thread(target=self._run_task, args=(bt, message), daemon=True)
        bt.handle.start()
        return bt.id

    def _next_id(self) -> str:
        return f"task_{secrets.token_hex(4)}"

    def _register(self, bt: BackgroundTask) -> None:
        with self._lock:
            self._tasks[bt.id] = bt
            if bt.name:
                self._by_name[bt.name] = bt.id

    def _run_task(self, bt: BackgroundTask, prompt: str) -> None:
        try:
            text = bt.runner.run_to_completion(
                prompt,
                bt.session,
                cancel_event=bt.cancel_event,
                on_event=lambda event: self._record_event(bt, event),
            )
            if bt.cancel_event.is_set():
                bt.status = Status.CANCELLED
            else:
                bt.result = text
                bt.status = Status.COMPLETED
        except BaseException as exc:
            if bt.cancel_event.is_set():
                bt.status = Status.CANCELLED
            else:
                bt.status = Status.FAILED
                bt.err = exc
        finally:
            bt.workspace_path = str(getattr(bt.runner, "workspace_path", "") or "")
            bt.branch = str(getattr(bt.runner, "branch", "") or "")
            cleanup = getattr(bt.runner, "cleanup", None)
            bt.cleanup = cleanup.summary() if cleanup is not None else ""
            bt.end_time = time.monotonic()
            self._notify_done(bt.id)

    def _record_event(self, bt: BackgroundTask, event: Any) -> None:
        if getattr(event, "type", "") == "tool_call_started" and getattr(event, "tool_call", None):
            bt.tool_count += 1
            bt.last_activity = event.tool_call.name

    def _notify_done(self, task_id: str) -> None:
        try:
            self._done.put_nowait(task_id)
        except Exception:
            pass
