from __future__ import annotations

import json
from typing import Any

from monkeycode.task.manager import BackgroundTask, Manager
from monkeycode.tools.base import ToolContext, ToolPolicy, ToolResult


class _TaskToolBase:
    def __init__(self, manager: Manager) -> None:
        self.manager = manager

    @property
    def is_system(self) -> bool:
        return True

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(tool_name=self.name, category="read", has_side_effects=False)


class TaskListTool(_TaskToolBase):
    name = "TaskList"
    description = "List background SubAgent tasks."
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(self.name, True, output=[_summary(task) for task in self.manager.list()])


class TaskGetTool(_TaskToolBase):
    name = "TaskGet"
    description = "Get one background SubAgent task by id."
    parameters_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        task = self.manager.get(str(arguments.get("task_id", "")))
        if task is None:
            return ToolResult(self.name, False, error_type="task_not_found", error_message="task not found")
        return ToolResult(self.name, True, output=_detail(task))


class TaskStopTool(_TaskToolBase):
    name = "TaskStop"
    description = "Request cancellation for a background SubAgent task."
    parameters_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    }

    @property
    def policy(self) -> ToolPolicy:
        # 取消 MonkeyCode 自己管理的后台任务不会修改 workspace，不应阻塞终端等待审批。
        return ToolPolicy(tool_name=self.name, category="read", has_side_effects=False)

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        if not self.manager.stop(str(arguments.get("task_id", ""))):
            return ToolResult(self.name, False, error_type="task_not_found", error_message="task not found")
        return ToolResult(self.name, True, output={"status": "cancellation_requested"})


class SendMessageTool(_TaskToolBase):
    name = "SendMessage"
    description = "Send a follow-up message to a completed named background SubAgent task."
    parameters_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "message": {"type": "string"}},
        "required": ["name", "message"],
    }

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(tool_name=self.name, category="side_effect", has_side_effects=True)

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            task_id = self.manager.send_message(str(arguments.get("name", "")), str(arguments.get("message", "")))
        except Exception as exc:
            return ToolResult(self.name, False, error_type="send_message_failed", error_message=str(exc))
        return ToolResult(self.name, True, output={"task_id": task_id, "status": "resumed"})


def _summary(task: BackgroundTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "status": str(task.status),
        "tool_count": task.tool_count,
        "last_activity": task.last_activity,
    }


def _detail(task: BackgroundTask) -> dict[str, Any]:
    data = _summary(task)
    data.update(
        {
            "start_time": task.start_time,
            "end_time": task.end_time,
            "result": task.result,
            "error": str(task.err) if task.err else "",
            "workspace_path": task.workspace_path,
            "branch": task.branch,
            "cleanup": task.cleanup,
        }
    )
    json.dumps(data, ensure_ascii=False)
    return data
