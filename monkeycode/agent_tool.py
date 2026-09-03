from __future__ import annotations

import contextvars
from dataclasses import replace
from pathlib import Path
import secrets
from typing import Any

from monkeycode.agent import AgentRunner
from monkeycode.agent_fork import build_forked_messages, is_fork_context
from monkeycode.events import AgentConfig
from monkeycode.hooks.engine import HookEngine
from monkeycode.memory import MemoryStore
from monkeycode.permissions import PermissionManager, PermissionMode, PermissionRuleStore
from monkeycode.providers.factory import create_provider
from monkeycode.session import ChatSession
from monkeycode.skills.catalog import Catalog as SkillCatalog
from monkeycode.subagent.catalog import Catalog
from monkeycode.task.manager import Manager
from monkeycode.tools.base import ToolContext, ToolPolicy, ToolResult
from monkeycode.tools.executor import ToolExecutor
from monkeycode.tools.filter import FilterParams, apply_agent_tool_filter
from monkeycode.worktree import WorktreeManager, validate_worktree_name

AUTO_BACKGROUND_SECONDS = 120
_SUB_AGENT_CONTEXT: contextvars.ContextVar[bool] = contextvars.ContextVar("sub_agent_context", default=False)


def is_sub_agent_context() -> bool:
    return _SUB_AGENT_CONTEXT.get()


class AgentTool:
    name = "Agent"

    def __init__(
        self,
        catalog: Catalog,
        task_mgr: Manager,
        *,
        parent: AgentRunner | None = None,
        bg_enabled: bool = True,
        worktree_manager: WorktreeManager | None = None,
    ) -> None:
        self.catalog = catalog
        self.task_mgr = task_mgr
        self.parent = parent
        self.bg_enabled = bg_enabled
        self.worktree_manager = worktree_manager

    @property
    def description(self) -> str:
        available = "; ".join(
            f"{definition.name}: {definition.description}"
            for definition in self.catalog.list()
        )
        return (
            "Launch a SubAgent. Use subagent_type for a named agent, or omit it "
            f"to fork current context. Available subagents ({len(self.catalog.list())}): "
            f"{available or 'none'}."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        names = [definition.name for definition in self.catalog.list()]
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "description": {"type": "string"},
                "subagent_type": {
                    "type": "string",
                    "enum": names,
                    "description": "Named SubAgent to launch. Omit to fork current context.",
                },
                "model": {"type": "string"},
                "run_in_background": {"type": "boolean"},
                "name": {"type": "string"},
                "isolation": {
                    "type": "string",
                    "enum": ["none", "worktree"],
                    "description": (
                        "Execution isolation for this call. Use worktree to run all child "
                        "file and command tools in a dedicated Git worktree."
                    ),
                },
            },
            "required": ["prompt"],
        }

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(tool_name=self.name, category="side_effect", has_side_effects=True)

    def set_parent(self, parent: AgentRunner) -> None:
        self.parent = parent

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return self._execute(
            arguments,
            context,
            inherit_hooks=True,
            force_background=False,
        )

    def execute_hook(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return self._execute(
            arguments,
            context,
            inherit_hooks=False,
            force_background=True,
        )

    def _execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext,
        *,
        inherit_hooks: bool,
        force_background: bool,
    ) -> ToolResult:
        if is_sub_agent_context():
            return _error("nested_agent_blocked", "Fork 子 Agent 不能再启动 Agent")
        if self.parent is None or self.parent.tool_executor is None:
            return _error("agent_parent_missing", "Agent tool has no parent runner")
        prompt = str(arguments.get("prompt") or "").strip()
        if not prompt:
            return _error("missing_prompt", "prompt is required")

        subagent_type = str(arguments.get("subagent_type") or "").strip()
        definition = self.catalog.resolve(subagent_type)
        if definition is None:
            return _error("unknown_subagent_type", f"unknown subagent_type: {subagent_type}")
        isolation = str(arguments.get("isolation", definition.isolation)).strip()
        if isolation not in {"none", "worktree"}:
            return _error("invalid_isolation", f"unknown isolation: {isolation}")

        background = force_background or bool(
            arguments.get("run_in_background") or definition.background or definition.is_fork()
        )
        if background and not self.bg_enabled:
            return _error("background_disabled", "SubAgent background execution is disabled")

        child_session = ChatSession()
        if definition.is_fork():
            if is_fork_context(self.parent_session_messages()):
                return _error("nested_agent_blocked", "Fork 子 Agent 不能再启动 Agent")
            child_session.replace_messages(build_forked_messages(self.parent_session_messages(), prompt))
            prompt_for_run = ""
        else:
            prompt_for_run = prompt

        task_name = str(arguments.get("name") or arguments.get("description") or definition.name)
        lease = None
        workspace_root = context.workspace_root
        if isolation == "worktree":
            if self.worktree_manager is None:
                return _error("worktree_unavailable", "Worktree isolation is not configured")
            requested_name = str(arguments.get("name") or "").strip()
            try:
                worktree_name = (
                    validate_worktree_name(requested_name)
                    if requested_name
                    else validate_worktree_name(
                        f"{definition.name.lower()}/{secrets.token_hex(4)}"
                    )
                )
                lease = self.worktree_manager.acquire(worktree_name)
                workspace_root = lease.path
            except Exception as exc:
                return _error("worktree_create_failed", str(exc))

        requested_model = str(arguments.get("model") or definition.model or "inherit").strip()
        try:
            runner = self._build_runner(
                definition,
                workspace_root,
                background=background,
                model=requested_model,
                worktree_lease=lease,
                inherit_hooks=inherit_hooks,
            )
        except Exception as exc:
            if lease is not None:
                lease.release()
            return _error("subagent_build_failed", f"{exc.__class__.__name__}: {exc}")
        guarded_runner = _SubAgentRunner(runner, lease=lease)
        if background:
            task_id = self.task_mgr.launch(guarded_runner, child_session, task_name, prompt_for_run)
            return ToolResult(self.name, True, output={"task_id": task_id, "status": "async_launched"})

        try:
            text = guarded_runner.run_to_completion(prompt_for_run, child_session)
        except Exception as exc:
            return _error("subagent_error", f"{exc.__class__.__name__}: {exc}")
        return ToolResult(self.name, True, output={"result": text})

    def parent_session_messages(self):
        return getattr(self.parent, "current_messages", []) if self.parent is not None else []

    def _build_runner(
        self,
        definition,
        workspace_root: Path,
        *,
        background: bool,
        model: str = "inherit",
        worktree_lease=None,
        inherit_hooks: bool = True,
    ) -> AgentRunner:
        parent_executor = self.parent.tool_executor
        all_names = parent_executor.registry.names()
        allowed = apply_agent_tool_filter(
            FilterParams(
                all=all_names,
                source=int(definition.source),
                background=background,
                allowed=definition.tools,
                disallowed=definition.disallowed_tools,
            )
        )
        if definition.dont_ask:
            permission_mode = PermissionMode.ALLOW
        elif definition.permission_mode is not None:
            permission_mode = definition.permission_mode
        else:
            permission_mode = parent_executor.permission_manager.mode
        rule_store = parent_executor.permission_manager.rule_store
        if workspace_root.resolve() != parent_executor.context.workspace_root.resolve():
            isolated_store = PermissionRuleStore.load(workspace_root)
            isolated_store.session_rules = list(rule_store.session_rules)
            rule_store = isolated_store
        pm = PermissionManager(
            mode=permission_mode,
            rule_store=rule_store,
            prompter=parent_executor.permission_manager.prompter,
        )
        child_executor = ToolExecutor(
            parent_executor.registry,
            workspace_root=workspace_root,
            default_timeout_seconds=parent_executor.context.default_timeout_seconds,
            max_output_chars=parent_executor.context.max_output_chars,
            permission_manager=pm,
        )
        max_iterations = definition.max_turns or self.parent.config.max_iterations
        provider = self.parent.provider
        if model and model != "inherit":
            parent_config = getattr(provider, "config", None)
            if parent_config is None:
                raise ValueError("SubAgent model override requires a configurable provider")
            provider = create_provider(replace(parent_config, model=model))
        system_prompt = definition.system_prompt
        memory_store = self.parent.memory_store
        skill_catalog = self.parent.skill_catalog
        if worktree_lease is not None:
            isolation_prompt = "\n".join(
                [
                    "<worktree_isolation>",
                    f"Main repository: {self.worktree_manager.repository_root}",
                    f"Isolated worktree: {workspace_root.resolve()}",
                    f"Branch: {worktree_lease.branch}",
                    "All file and command operations must stay inside the isolated worktree.",
                    "</worktree_isolation>",
                ]
            )
            system_prompt = f"{system_prompt.rstrip()}\n\n{isolation_prompt}".strip()
            memory_store = MemoryStore(workspace_root, self.parent.user_home)
            skill_catalog = SkillCatalog.load(workspace_root)
        hook_engine = self.parent.hook_engine if inherit_hooks else None
        isolated_hook_engine = None
        if worktree_lease is not None and hook_engine is not None:
            isolated_hook_engine = HookEngine(
                hook_engine.config,
                tool_executor=child_executor,
            )
            hook_engine = isolated_hook_engine
        runner = AgentRunner(
            provider,
            tool_executor=child_executor,
            config=AgentConfig(
                max_iterations=max_iterations,
                default_tool_timeout_seconds=child_executor.context.default_timeout_seconds,
                max_output_chars=child_executor.context.max_output_chars,
            ),
            context_config=self.parent.context_manager.config,
            skill_catalog=skill_catalog,
            active_skills=None,
            allowed_tool_names=allowed,
            hook_engine=hook_engine,
            system_prompt=system_prompt,
            memory_store=memory_store,
            user_home=self.parent.user_home,
        )
        runner._isolated_hook_engine = isolated_hook_engine
        return runner


class _SubAgentRunner:
    def __init__(self, runner: AgentRunner, *, lease=None) -> None:
        self.runner = runner
        self.lease = lease
        self.workspace_path = str(lease.path) if lease is not None else ""
        self.branch = lease.branch if lease is not None else ""
        self.cleanup = None

    def run_to_completion(self, *args, **kwargs) -> str:
        token = _SUB_AGENT_CONTEXT.set(True)
        try:
            text = self.runner.run_to_completion(*args, **kwargs)
        except BaseException:
            if self.lease is not None:
                self.cleanup = self.lease.release()
            raise
        finally:
            _SUB_AGENT_CONTEXT.reset(token)
            hook_engine = getattr(self.runner, "_isolated_hook_engine", None)
            if hook_engine is not None:
                hook_engine.close()
        if self.lease is not None:
            self.cleanup = self.lease.release()
            text = f"{text}\n\n<worktree-cleanup>{self.cleanup.summary()}</worktree-cleanup>"
        return text


def _error(error_type: str, message: str) -> ToolResult:
    return ToolResult("Agent", False, error_type=error_type, error_message=message)
