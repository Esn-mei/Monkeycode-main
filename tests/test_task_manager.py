import time

import pytest

from monkeycode.session import ChatSession
from monkeycode.task.manager import Manager, Status, TaskBusy


class MockRunner:
    def __init__(self, result: str = "done", delay: float = 0.0, fail: bool = False) -> None:
        self.result = result
        self.delay = delay
        self.fail = fail

    def run_to_completion(self, prompt, session, *, cancel_event=None, on_event=None):
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        session.add_user_message(prompt)
        session.add_assistant_message(self.result)
        return self.result


class CleanupRunner(MockRunner):
    workspace_path = "C:/repo/.monkeycode/worktrees/worker"
    branch = "monkeycode/worktree/worker"

    class Cleanup:
        def summary(self):
            return "Worktree retained: dirty"

    cleanup = Cleanup()


def test_launch_completes() -> None:
    manager = Manager()
    task_id = manager.launch(MockRunner("ok"), ChatSession(), "worker", "hi")

    assert manager.subscribe_done().get(timeout=2) == task_id
    task = manager.get(task_id)
    assert task.status == Status.COMPLETED
    assert task.result == "ok"


def test_launch_failure() -> None:
    manager = Manager()
    task_id = manager.launch(MockRunner(fail=True), ChatSession(), "worker", "hi")

    manager.subscribe_done().get(timeout=2)
    assert manager.get(task_id).status == Status.FAILED
    assert "boom" in str(manager.get(task_id).err)


def test_launch_records_worktree_details() -> None:
    manager = Manager()
    task_id = manager.launch(CleanupRunner("ok"), ChatSession(), "worker", "hi")

    manager.subscribe_done().get(timeout=2)
    task = manager.get(task_id)
    assert task.workspace_path.endswith("worktrees/worker")
    assert task.branch == "monkeycode/worktree/worker"
    assert task.cleanup == "Worktree retained: dirty"


def test_stop() -> None:
    manager = Manager()
    task_id = manager.launch(MockRunner(delay=0.2), ChatSession(), "worker", "hi")

    assert manager.stop(task_id) is True
    assert manager.get(task_id).status == Status.CANCELLED


def test_send_message_after_complete() -> None:
    manager = Manager()
    task_id = manager.launch(MockRunner("first"), ChatSession(), "worker", "hi")
    manager.subscribe_done().get(timeout=2)

    assert manager.send_message("worker", "again") == task_id
    manager.subscribe_done().get(timeout=2)
    assert manager.get(task_id).result == "first"


def test_send_message_busy() -> None:
    manager = Manager()
    manager.launch(MockRunner(delay=0.2), ChatSession(), "worker", "hi")

    with pytest.raises(TaskBusy):
        manager.send_message("worker", "again")
