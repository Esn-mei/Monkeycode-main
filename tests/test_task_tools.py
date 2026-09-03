from monkeycode.session import ChatSession
from monkeycode.task.manager import Manager
from monkeycode.task.tools import SendMessageTool, TaskGetTool, TaskListTool, TaskStopTool
from monkeycode.tools.base import ToolContext
from monkeycode.tools import create_default_executor, create_default_registry
from monkeycode.messages import ToolCall


class MockRunner:
    def __init__(self, result: str = "done", delay: float = 0.0) -> None:
        self.result = result
        self.delay = delay

    def run_to_completion(self, prompt, session, *, cancel_event=None, on_event=None):
        if self.delay:
            import time

            time.sleep(self.delay)
        session.add_user_message(prompt)
        session.add_assistant_message(self.result)
        return self.result


def ctx(tmp_path):
    return ToolContext(workspace_root=tmp_path)


def test_task_tools(tmp_path) -> None:
    manager = Manager()
    task_id = manager.launch(MockRunner("ok"), ChatSession(), "worker", "hi")
    manager.subscribe_done().get(timeout=2)

    assert TaskListTool(manager).execute({}, ctx(tmp_path)).success is True
    assert TaskGetTool(manager).execute({"task_id": task_id}, ctx(tmp_path)).output["id"] == task_id
    assert SendMessageTool(manager).execute({"name": "worker", "message": "again"}, ctx(tmp_path)).success is True


def test_task_get_unknown(tmp_path) -> None:
    result = TaskGetTool(Manager()).execute({"task_id": "missing"}, ctx(tmp_path))
    assert result.success is False
    assert result.error_type == "task_not_found"


def test_task_stop(tmp_path) -> None:
    manager = Manager()
    task_id = manager.launch(MockRunner(delay=0.2), ChatSession(), "worker", "hi")
    result = TaskStopTool(manager).execute({"task_id": task_id}, ctx(tmp_path))

    assert result.success is True


def test_task_stop_does_not_require_workspace_permission(tmp_path) -> None:
    manager = Manager()
    task_id = manager.launch(MockRunner(delay=0.2), ChatSession(), "worker", "hi")
    registry = create_default_registry()
    registry.register(TaskStopTool(manager))
    executor = create_default_executor(tmp_path, registry=registry)

    result = executor.execute(
        ToolCall(
            id="stop_1",
            name="TaskStop",
            arguments_json="",
            arguments={"task_id": task_id},
        )
    )

    assert result.success is True
