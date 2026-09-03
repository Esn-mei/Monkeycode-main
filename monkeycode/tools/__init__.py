from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from monkeycode.tools.commands import RunCommandTool
from monkeycode.tools.executor import ToolExecutor
from monkeycode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from monkeycode.tools.registry import ToolRegistry
from monkeycode.tools.search import FindFilesTool, SearchCodeTool

if TYPE_CHECKING:
    from monkeycode.permissions import PermissionManager, PermissionMode, PermissionPrompter, PermissionRuleStore


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in [
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        RunCommandTool(),
        FindFilesTool(),
        SearchCodeTool(),
    ]:
        registry.register(tool)
    return registry


def create_default_executor(
    workspace_root: str | Path,
    *,
    registry: ToolRegistry | None = None,
    default_timeout_seconds: float = 10.0,
    max_output_chars: int = 12000,
    permission_mode: PermissionMode | str = "default",
    permission_manager: PermissionManager | None = None,
    permission_prompter: PermissionPrompter | None = None,
    permission_rule_store: PermissionRuleStore | None = None,
    load_permission_rules: bool = False,
) -> ToolExecutor:
    from monkeycode.permissions import PermissionRuleStore

    root = Path(workspace_root)
    rule_store = permission_rule_store
    if rule_store is None:
        rule_store = (
            PermissionRuleStore.load(root)
            if load_permission_rules
            else PermissionRuleStore.empty(root)
        )
    return ToolExecutor(
        registry or create_default_registry(),
        workspace_root=root,
        default_timeout_seconds=default_timeout_seconds,
        max_output_chars=max_output_chars,
        permission_mode=permission_mode,
        permission_manager=permission_manager,
        permission_prompter=permission_prompter,
        permission_rule_store=rule_store,
    )
