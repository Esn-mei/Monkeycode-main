from pathlib import Path
import json
import subprocess
import threading

from monkeycode.agent import AgentRunner
from monkeycode.agent_tool import AgentTool, _SUB_AGENT_CONTEXT
from monkeycode.config import AppConfig, SecretValue
from monkeycode.hooks.engine import HookEngine
from monkeycode.messages import StreamEvent, ToolCall
from monkeycode.providers.openai import OpenAIProvider
from monkeycode.session import ChatSession
from monkeycode.subagent.catalog import Catalog
from monkeycode.subagent.definition import Definition, Source
from monkeycode.task.manager import Manager
from monkeycode.tools import create_default_executor, create_default_registry
from monkeycode.tools.base import ToolContext
from monkeycode.worktree import WorktreeManager


class TextProvider:
    def __init__(self, text: str = "child done") -> None:
        self.text = text
        self.calls = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append((list(messages), tools, prompt_payload))
        yield StreamEvent(type="text_delta", text=self.text)
        yield StreamEvent(type="done")


class ToolWritingProvider(TextProvider):
    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append((list(messages), tools, prompt_payload))
        if len(self.calls) == 1:
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id="write_1",
                    name="write_file",
                    arguments_json='{"path":"child.txt","content":"isolated"}',
                    arguments={"path": "child.txt", "content": "isolated"},
                ),
            )
        else:
            yield StreamEvent(type="text_delta", text="child changed")
        yield StreamEvent(type="done")


class ConcurrentToolWritingProvider(TextProvider):
    def __init__(self) -> None:
        super().__init__()
        self._local = threading.local()

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        iteration = getattr(self._local, "iteration", 0)
        self._local.iteration = iteration + 1
        if iteration == 0:
            content = threading.current_thread().name
            arguments = {"path": "shared.txt", "content": content}
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id=f"write_{content}",
                    name="write_file",
                    arguments_json=json.dumps(arguments),
                    arguments=arguments,
                ),
            )
        else:
            yield StreamEvent(type="text_delta", text="child changed")
        yield StreamEvent(type="done")


class WitnessCommitProvider(TextProvider):
    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append((list(messages), tools, prompt_payload))
        if len(self.calls) == 1:
            tool_call = ToolCall(
                id="write_witness",
                name="write_file",
                arguments_json="",
                arguments={"path": "witness.txt", "content": "modified by isolated worker"},
            )
            yield StreamEvent(type="tool_call", tool_call=tool_call)
        elif len(self.calls) == 2:
            tool_call = ToolCall(
                id="commit_witness",
                name="run_command",
                arguments_json="",
                arguments={
                    "command": (
                        'git add witness.txt && git commit -m "modify witness in isolated worker"'
                    )
                },
            )
            yield StreamEvent(type="tool_call", tool_call=tool_call)
        else:
            yield StreamEvent(type="text_delta", text="isolated commit complete")
        yield StreamEvent(type="done")


def make_tool(tmp_path: Path):
    catalog = Catalog()
    catalog.add(
        Definition(
            name="tester",
            description="test agent",
            system_prompt="always answer as tester",
            source=Source.PROJECT,
        )
    )
    manager = Manager()
    registry = create_default_registry()
    executor = create_default_executor(tmp_path, registry=registry, permission_mode="allow")
    parent = AgentRunner(TextProvider(), tool_executor=executor)
    return AgentTool(catalog, manager, parent=parent), manager, parent


def test_agent_tool_missing_prompt(tmp_path: Path) -> None:
    tool, _, _ = make_tool(tmp_path)

    result = tool.execute({}, ToolContext(tmp_path))

    assert result.success is False
    assert result.error_type == "missing_prompt"


def test_hook_agent_launches_in_background_without_inheriting_hooks(tmp_path: Path) -> None:
    tool, manager, parent = make_tool(tmp_path)
    parent.hook_engine = HookEngine()

    result = tool.execute_hook(
        {
            "prompt": "inspect the hook event",
            "subagent_type": "tester",
            "name": "hook-worker",
        },
        ToolContext(tmp_path),
    )

    assert result.success is True
    assert result.output["status"] == "async_launched"
    task = manager.get(result.output["task_id"])
    assert task is not None
    assert task.runner.runner.hook_engine is None


def test_hook_agent_reports_background_disabled(tmp_path: Path) -> None:
    tool, _, _ = make_tool(tmp_path)
    tool.bg_enabled = False

    result = tool.execute_hook(
        {"prompt": "inspect", "subagent_type": "tester"},
        ToolContext(tmp_path),
    )

    assert result.success is False
    assert result.error_type == "background_disabled"


def test_regular_agent_inherits_parent_hooks(tmp_path: Path) -> None:
    tool, _, parent = make_tool(tmp_path)
    parent.hook_engine = HookEngine()
    definition = tool.catalog.resolve("tester")

    runner = tool._build_runner(definition, tmp_path, background=False)

    assert runner.hook_engine is parent.hook_engine


def test_agent_tool_definition_exposes_available_subagents(tmp_path: Path) -> None:
    tool, _, _ = make_tool(tmp_path)

    assert "Available subagents (1)" in tool.description
    assert "tester: test agent" in tool.description
    assert tool.parameters_schema["properties"]["subagent_type"]["enum"] == ["tester"]
    assert tool.parameters_schema["properties"]["isolation"]["enum"] == ["none", "worktree"]


def test_agent_tool_unknown_subagent(tmp_path: Path) -> None:
    tool, _, _ = make_tool(tmp_path)

    result = tool.execute({"prompt": "hi", "subagent_type": "missing"}, ToolContext(tmp_path))

    assert result.success is False
    assert result.error_type == "unknown_subagent_type"


def test_agent_tool_runs_known_subagent(tmp_path: Path) -> None:
    tool, _, parent = make_tool(tmp_path)

    result = tool.execute({"prompt": "hi", "subagent_type": "tester"}, ToolContext(tmp_path))

    assert result.success is True
    assert result.output["result"] == "child done"
    assert "always answer as tester" in parent.provider.calls[0][2].stable_system_text


def test_agent_tool_background(tmp_path: Path) -> None:
    tool, manager, _ = make_tool(tmp_path)

    result = tool.execute(
        {"prompt": "hi", "subagent_type": "tester", "run_in_background": True},
        ToolContext(tmp_path),
    )

    assert result.success is True
    assert result.output["status"] == "async_launched"
    task_id = result.output["task_id"]
    manager.subscribe_done().get(timeout=2)
    assert manager.get(task_id).result == "child done"


def test_agent_tool_nested_blocked(tmp_path: Path) -> None:
    tool, _, _ = make_tool(tmp_path)
    token = _SUB_AGENT_CONTEXT.set(True)
    try:
        result = tool.execute({"prompt": "hi"}, ToolContext(tmp_path))
    finally:
        _SUB_AGENT_CONTEXT.reset(token)

    assert result.success is False
    assert result.error_type == "nested_agent_blocked"


def test_build_runner_uses_definition_model(tmp_path: Path) -> None:
    tool, _, parent = make_tool(tmp_path)
    parent.provider = OpenAIProvider(
        AppConfig(
            protocol="openai",
            model="deepseek-v4-pro",
            base_url="https://example.invalid",
            api_key=SecretValue("test"),
        )
    )
    definition = tool.catalog.resolve("tester")

    runner = tool._build_runner(
        definition,
        tmp_path,
        background=False,
        model="deepseek-v4-flash",
    )

    assert runner.provider.config.model == "deepseek-v4-flash"
    assert parent.provider.config.model == "deepseek-v4-pro"


def test_build_runner_inherit_reuses_parent_provider(tmp_path: Path) -> None:
    tool, _, parent = make_tool(tmp_path)
    definition = tool.catalog.resolve("tester")

    runner = tool._build_runner(definition, tmp_path, background=False)

    assert runner.provider is parent.provider


def test_agent_tool_runs_isolated_agent_in_worktree(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True)

    catalog = Catalog()
    catalog.add(
        Definition(
            name="isolated",
            description="isolated agent",
            isolation="worktree",
            system_prompt="work alone",
            source=Source.PROJECT,
        )
    )
    manager = Manager()
    executor = create_default_executor(tmp_path, permission_mode="allow")
    parent = AgentRunner(TextProvider(), tool_executor=executor)
    tool = AgentTool(
        catalog,
        manager,
        parent=parent,
        worktree_manager=WorktreeManager(tmp_path),
    )

    result = tool.execute(
        {"prompt": "hi", "subagent_type": "isolated", "name": "workers/one"},
        ToolContext(tmp_path),
    )

    assert result.success is True
    assert "<worktree-cleanup>Worktree cleaned:" in result.output["result"]
    assert not (tmp_path / ".monkeycode" / "worktrees" / "workers" / "one").exists()
    prompt = parent.provider.calls[0][2].stable_system_text
    assert "<worktree_isolation>" in prompt
    assert "workers\\one" in prompt or "workers/one" in prompt


def test_agent_tool_call_can_request_worktree_isolation(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("main", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True)

    catalog = Catalog()
    catalog.add(
        Definition(
            name="general-purpose",
            description="general agent",
            source=Source.BUILTIN,
        )
    )
    provider = ToolWritingProvider()
    executor = create_default_executor(tmp_path, permission_mode="allow")
    parent = AgentRunner(provider, tool_executor=executor)
    tool = AgentTool(
        catalog,
        Manager(),
        parent=parent,
        worktree_manager=WorktreeManager(tmp_path),
    )

    result = tool.execute(
        {
            "prompt": "write",
            "subagent_type": "general-purpose",
            "isolation": "worktree",
            "name": "workers/requested",
        },
        ToolContext(tmp_path),
    )

    worktree = tmp_path / ".monkeycode" / "worktrees" / "workers" / "requested"
    assert result.success is True
    assert (worktree / "child.txt").read_text(encoding="utf-8") == "isolated"
    assert not (tmp_path / "child.txt").exists()


def test_requested_worktree_isolation_keeps_committed_witness_out_of_main_tree(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "witness.txt").write_text("original content from main agent", encoding="utf-8")
    subprocess.run(["git", "add", "witness.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True)

    catalog = Catalog()
    catalog.add(
        Definition(
            name="general-purpose",
            description="general agent",
            source=Source.BUILTIN,
        )
    )
    provider = WitnessCommitProvider()
    executor = create_default_executor(tmp_path, permission_mode="allow")
    parent = AgentRunner(provider, tool_executor=executor)
    tool = AgentTool(
        catalog,
        Manager(),
        parent=parent,
        worktree_manager=WorktreeManager(tmp_path),
    )

    result = tool.execute(
        {
            "prompt": (
                '把 witness.txt 的内容改成 "modified by isolated worker"，然后用 git 提交。'
            ),
            "subagent_type": "general-purpose",
            "isolation": "worktree",
            "name": "workers/witness",
        },
        ToolContext(tmp_path),
    )

    worktree = tmp_path / ".monkeycode" / "worktrees" / "workers" / "witness"
    assert result.success is True
    assert (tmp_path / "witness.txt").read_text(encoding="utf-8") == (
        "original content from main agent"
    )
    assert (worktree / "witness.txt").read_text(encoding="utf-8") == (
        "modified by isolated worker"
    )
    assert (
        subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "modify witness in isolated worker"
    )


def test_isolated_agent_tools_use_worktree_cwd_and_retain_dirty_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True)

    catalog = Catalog()
    catalog.add(
        Definition(
            name="isolated",
            description="isolated agent",
            isolation="worktree",
            dont_ask=True,
            source=Source.PROJECT,
        )
    )
    provider = ToolWritingProvider()
    executor = create_default_executor(tmp_path, permission_mode="allow")
    parent = AgentRunner(provider, tool_executor=executor)
    tool = AgentTool(
        catalog,
        Manager(),
        parent=parent,
        worktree_manager=WorktreeManager(tmp_path),
    )

    result = tool.execute(
        {"prompt": "write", "subagent_type": "isolated", "name": "workers/dirty"},
        ToolContext(tmp_path),
    )

    worktree = tmp_path / ".monkeycode" / "worktrees" / "workers" / "dirty"
    assert result.success is True
    assert "Worktree retained:" in result.output["result"]
    assert (worktree / "child.txt").read_text(encoding="utf-8") == "isolated"
    assert not (tmp_path / "child.txt").exists()


def test_background_isolated_agent_records_cleanup_details(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True)

    catalog = Catalog()
    catalog.add(
        Definition(
            name="isolated",
            description="isolated agent",
            isolation="worktree",
            source=Source.PROJECT,
        )
    )
    task_manager = Manager()
    executor = create_default_executor(tmp_path, permission_mode="allow")
    parent = AgentRunner(TextProvider(), tool_executor=executor)
    tool = AgentTool(
        catalog,
        task_manager,
        parent=parent,
        worktree_manager=WorktreeManager(tmp_path),
    )

    launched = tool.execute(
        {
            "prompt": "hi",
            "subagent_type": "isolated",
            "name": "workers/background",
            "run_in_background": True,
        },
        ToolContext(tmp_path),
    )
    task_id = launched.output["task_id"]
    task_manager.subscribe_done().get(timeout=3)
    task = task_manager.get(task_id)

    assert task.workspace_path.endswith("workers\\background") or task.workspace_path.endswith(
        "workers/background"
    )
    assert task.branch == "monkeycode/worktree/workers/background"
    assert "Worktree cleaned:" in task.cleanup


def test_parallel_isolated_agents_do_not_share_files_or_process_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "shared.txt").write_text("main", encoding="utf-8")
    subprocess.run(["git", "add", "shared.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True)
    monkeypatch.setattr("monkeycode.agent_tool.SkillCatalog.load", lambda root: None)

    catalog = Catalog()
    catalog.add(
        Definition(
            name="isolated",
            description="isolated agent",
            isolation="worktree",
            dont_ask=True,
            source=Source.PROJECT,
        )
    )
    manager = Manager()
    executor = create_default_executor(tmp_path, permission_mode="allow")
    parent = AgentRunner(ConcurrentToolWritingProvider(), tool_executor=executor)
    tool = AgentTool(
        catalog,
        manager,
        parent=parent,
        worktree_manager=WorktreeManager(tmp_path),
    )
    process_cwd = Path.cwd()

    first = tool.execute(
        {
            "prompt": "first",
            "subagent_type": "isolated",
            "name": "parallel/first",
            "run_in_background": True,
        },
        ToolContext(tmp_path),
    )
    second = tool.execute(
        {
            "prompt": "second",
            "subagent_type": "isolated",
            "name": "parallel/second",
            "run_in_background": True,
        },
        ToolContext(tmp_path),
    )
    manager.subscribe_done().get(timeout=5)
    manager.subscribe_done().get(timeout=5)

    first_path = tmp_path / ".monkeycode" / "worktrees" / "parallel" / "first"
    second_path = tmp_path / ".monkeycode" / "worktrees" / "parallel" / "second"
    first_content = (first_path / "shared.txt").read_text(encoding="utf-8")
    second_content = (second_path / "shared.txt").read_text(encoding="utf-8")
    assert first.success is True
    assert second.success is True
    assert first_content != second_content
    assert (tmp_path / "shared.txt").read_text(encoding="utf-8") == "main"
    assert Path.cwd() == process_cwd
