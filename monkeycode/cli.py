from __future__ import annotations

import argparse
import sys
from pathlib import Path

from monkeycode.config import load_config
from monkeycode.errors import MonkeyCodeError
from monkeycode.hooks.config import load_hook_config
from monkeycode.hooks.engine import HookEngine
from monkeycode.mcp import McpToolManager, load_mcp_config
from monkeycode.permissions import PermissionMode
from monkeycode.providers.factory import create_provider
from monkeycode.agent_tool import AgentTool
from monkeycode.subagent import load_catalog as load_subagent_catalog
from monkeycode.task import Manager, SendMessageTool, TaskGetTool, TaskListTool, TaskStopTool
from monkeycode.tools import create_default_executor, create_default_registry
from monkeycode.tui import run_chat_loop
from monkeycode.worktree import WorktreeCleaner, WorktreeManager

PERMISSION_MODE_ALIASES = {
    "default": PermissionMode.DEFAULT.value,
    "default permissions": PermissionMode.DEFAULT.value,
    "strict": PermissionMode.STRICT.value,
    "auto-review": PermissionMode.STRICT.value,
    "auto review": PermissionMode.STRICT.value,
    "allow": PermissionMode.ALLOW.value,
    "full access": PermissionMode.ALLOW.value,
}


def resolve_config_path(path: Path | None, *, cwd: Path | None = None) -> Path:
    if path is not None:
        return path
    base = cwd or Path.cwd()
    monkeycode_config = base / "monkeycode.yaml"
    if monkeycode_config.exists():
        return monkeycode_config
    return base / "config.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="monkeycode")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--permission-mode",
        type=parse_permission_mode,
        default=PermissionMode.DEFAULT.value,
        help="Permission mode: Default permissions, Auto-review, or Full access",
    )
    parser.add_argument(
        "--resume-session",
        default=None,
        help="Resume a MonkeyCode session id from .monkeycode/sessions",
    )
    return parser


def parse_permission_mode(value: str) -> str:
    normalized = value.strip().lower().replace("_", " ")
    if normalized in PERMISSION_MODE_ALIASES:
        return PERMISSION_MODE_ALIASES[normalized]
    raise argparse.ArgumentTypeError(
        "permission mode must be Default permissions, Auto-review, or Full access"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    tool_executor = None
    hook_engine = None
    worktree_cleaner = None

    try:
        workspace_root = Path.cwd()
        config = load_config(resolve_config_path(args.config))
        provider = create_provider(config)
        registry = create_default_registry()
        subagent_catalog = load_subagent_catalog(workspace_root)
        task_mgr = Manager()
        worktree_manager = WorktreeManager(workspace_root, config.worktree)
        worktree_cleaner = WorktreeCleaner(worktree_manager)
        worktree_cleaner.start()
        for tool in [
            TaskListTool(task_mgr),
            TaskGetTool(task_mgr),
            TaskStopTool(task_mgr),
            SendMessageTool(task_mgr),
        ]:
            registry.register(tool)
        agent_tool = AgentTool(
            subagent_catalog,
            task_mgr,
            bg_enabled=config.effective_enable_subagent_background(),
            worktree_manager=worktree_manager,
        )
        registry.register(agent_tool)
        mcp_manager = McpToolManager(load_mcp_config(workspace_root), workspace_root)
        mcp_manager.register_tools(registry)
        tool_executor = create_default_executor(
            workspace_root,
            registry=registry,
            permission_mode=args.permission_mode,
            load_permission_rules=True,
        )
        tool_executor.add_close_callback(mcp_manager.close)
        tool_executor.add_close_callback(worktree_cleaner.stop)
        hook_config = load_hook_config(workspace_root)
        for issue in hook_config.issues:
            print(f"Hook config warning: {issue.format()}", file=sys.stderr)
        hook_engine = HookEngine(
            hook_config,
            tool_executor=tool_executor,
            subagent_launcher=lambda arguments: agent_tool.execute_hook(
                arguments,
                tool_executor.context,
            ),
        )
        hook_engine.dispatch(
            "system.config_loaded",
            {
                "workspace_root": str(workspace_root),
                "config": {"path": str(resolve_config_path(args.config))},
            },
        )
        hook_engine.dispatch(
            "system.hooks_loaded",
            {
                "workspace_root": str(workspace_root),
                "hooks": {
                    "loaded_count": len(hook_config.rules),
                    "invalid_count": len(hook_config.issues),
                },
            },
        )
        tool_executor.add_close_callback(hook_engine.close)
    except MonkeyCodeError as exc:
        if tool_executor is not None:
            tool_executor.close()
        elif worktree_cleaner is not None:
            worktree_cleaner.stop()
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        return run_chat_loop(
            config,
            provider,
            tool_executor=tool_executor,
            workspace_root=Path.cwd(),
            resume_session_id=args.resume_session,
            enable_local_state=True,
            hook_engine=hook_engine,
            task_mgr=task_mgr,
            subagent_catalog=subagent_catalog,
            agent_tool=agent_tool,
        )
    finally:
        if tool_executor is not None:
            tool_executor.close()
        elif worktree_cleaner is not None:
            worktree_cleaner.stop()
