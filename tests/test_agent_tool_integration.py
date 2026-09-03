import json
from pathlib import Path

from monkeycode.agent import AgentRunner
from monkeycode.agent_tool import AgentTool
from monkeycode.messages import StreamEvent, ToolCall
from monkeycode.session import ChatSession
from monkeycode.subagent.catalog import Catalog
from monkeycode.subagent.definition import Definition, Source
from monkeycode.task.manager import Manager
from monkeycode.task.tools import SendMessageTool, TaskGetTool, TaskListTool, TaskStopTool
from monkeycode.tools import create_default_executor, create_default_registry


class ScriptedProvider:
    def __init__(self, turns) -> None:
        self.turns = list(turns)
        self.calls = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append((list(messages), tools, prompt_payload))
        yield from self.turns.pop(0)


def call(name: str, arguments: dict, ident: str = "call_1") -> ToolCall:
    return ToolCall(
        id=ident,
        name=name,
        arguments_json=json.dumps(arguments, ensure_ascii=False),
        arguments=arguments,
    )


def test_main_agent_invokes_agent_tool_end_to_end(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=call("Agent", {"prompt": "inspect README", "subagent_type": "Explore"}),
                ),
                StreamEvent(type="done"),
            ],
            [StreamEvent(type="text_delta", text="Scope: README checked"), StreamEvent(type="done")],
            [StreamEvent(type="text_delta", text="子 Agent 说 README checked"), StreamEvent(type="done")],
        ]
    )
    catalog = Catalog()
    catalog.add(
        Definition(
            name="Explore",
            description="read only",
            system_prompt="只读探索",
            disallowed_tools=["write_file", "edit_file"],
            source=Source.BUILTIN,
        )
    )
    manager = Manager()
    registry = create_default_registry()
    agent_tool = AgentTool(catalog, manager)
    for tool in [
        TaskListTool(manager),
        TaskGetTool(manager),
        TaskStopTool(manager),
        SendMessageTool(manager),
        agent_tool,
    ]:
        registry.register(tool)
    executor = create_default_executor(tmp_path, registry=registry, permission_mode="allow")
    runner = AgentRunner(provider, tool_executor=executor)
    agent_tool.set_parent(runner)
    session = ChatSession()

    events = list(runner.run_turn("用 Explore 子 Agent 检查 README", session))

    assert events[-1].stop_reason == "model_done"
    assert session.messages[-1].content == "子 Agent 说 README checked"
    tool_payload = json.loads(session.messages[-2].content)
    assert tool_payload["output"]["result"] == "Scope: README checked"
    child_tools = {tool.name for tool in provider.calls[1][1]}
    assert "Agent" not in child_tools
    assert "write_file" not in child_tools
