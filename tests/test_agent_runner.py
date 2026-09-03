from __future__ import annotations

from pathlib import Path
import json

from monkeycode.agent import AgentRunner
from monkeycode.config import ContextConfig
from monkeycode.events import AgentConfig, CancellationToken
from monkeycode.memory import MemoryStore
from monkeycode.messages import StreamEvent, ToolCall
from monkeycode.session import ChatSession
from monkeycode.session_archive import SessionArchive
from monkeycode.skills.active import ActiveSkills
from monkeycode.skills.catalog import Catalog
from monkeycode.skills.types import Skill, SkillMeta, SkillSource
from monkeycode.tools import create_default_executor
from monkeycode.tools.base import ToolContext, ToolPolicy, ToolResult
from monkeycode.tools.executor import ToolExecutor
from monkeycode.tools.registry import ToolRegistry


class ScriptedProvider:
    def __init__(self, turns) -> None:
        self.turns = list(turns)
        self.calls = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True):
        self.calls.append((list(messages), tools, allow_tool_calls))
        events = self.turns.pop(0)
        if isinstance(events, Exception):
            raise events
        yield from events


class PromptCaptureProvider:
    def __init__(self) -> None:
        self.calls = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append(
            {
                "messages": list(messages),
                "stable_system_text": prompt_payload.stable_system_text,
                "dynamic_system_messages": list(prompt_payload.dynamic_system_messages),
            }
        )
        yield StreamEvent(type="text_delta", text="ok")
        yield StreamEvent(type="done")


class RecordingMemoryStore:
    def __init__(self) -> None:
        self.snapshots = []

    def combined_index(self) -> str:
        return "- preference: 中文回答"

    def schedule_update(self, provider, snapshot):
        self.snapshots.append(snapshot)
        class Done:
            pass
        return Done()


def tool_call(name: str, arguments: dict, ident: str = "call_1") -> ToolCall:
    import json

    return ToolCall(id=ident, name=name, arguments_json=json.dumps(arguments), arguments=arguments)


def test_agent_runner_handles_plain_chat() -> None:
    provider = ScriptedProvider([[StreamEvent(type="text_delta", text="Hi"), StreamEvent(type="done")]])
    session = ChatSession()
    runner = AgentRunner(provider)

    events = list(runner.run_turn("hello", session))

    assert [event.text for event in events if event.type == "text_delta"] == ["Hi"]
    assert events[-1].stop_reason == "model_done"
    assert [message.role for message in session.messages] == ["user", "assistant"]


def test_agent_runner_runs_tool_then_continues(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("MonkeyCode", encoding="utf-8")
    provider = ScriptedProvider(
        [
            [
                StreamEvent(type="reasoning_delta", text="Need file."),
                StreamEvent(
                    type="tool_call",
                    tool_call=tool_call("read_file", {"path": "README.md"}),
                ),
                StreamEvent(type="done"),
            ],
            [StreamEvent(type="text_delta", text="Read it."), StreamEvent(type="done")],
        ]
    )
    session = ChatSession()
    runner = AgentRunner(provider, tool_executor=create_default_executor(tmp_path))

    events = list(runner.run_turn("read", session))

    assert provider.calls[1][0][-1].role == "tool"
    assert provider.calls[1][0][-2].provider_payload == {"reasoning_content": "Need file."}
    assert any(event.type == "tool_result" for event in events)
    assert events[-1].stop_reason == "model_done"


def test_agent_runner_reuses_duplicate_search_across_iterations(tmp_path: Path) -> None:
    class CountingSearch:
        name = "search_code"
        description = "Search."
        parameters_schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        policy = ToolPolicy(
            tool_name=name,
            category="read",
            allowed_in_plan_mode=True,
            can_run_parallel=True,
            has_side_effects=False,
        )

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, arguments, context: ToolContext) -> ToolResult:
            self.calls += 1
            return ToolResult(
                tool_name=self.name,
                success=True,
                output={"matches": [{"path": "agent.md", "line": 1, "text": "name: Explore"}]},
            )

    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=tool_call("search_code", {"query": "name:"}, "call_1"),
                ),
                StreamEvent(type="done"),
            ],
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=tool_call("search_code", {"query": "name:"}, "call_2"),
                ),
                StreamEvent(type="done"),
            ],
            [StreamEvent(type="text_delta", text="Explore"), StreamEvent(type="done")],
        ]
    )
    search = CountingSearch()
    registry = ToolRegistry()
    registry.register(search)
    executor = ToolExecutor(registry, workspace_root=tmp_path)
    session = ChatSession()
    runner = AgentRunner(provider, tool_executor=executor)

    events = list(runner.run_turn("有哪些 Agent", session))

    assert search.calls == 1
    duplicate_payload = json.loads(provider.calls[2][0][-1].content)
    assert duplicate_payload["metadata"]["deduplicated"] is True
    assert duplicate_payload["metadata"]["original_tool_call_id"] == "call_1"
    assert any(event.text == "Explore" for event in events)


def test_agent_runner_archives_large_tool_result_before_next_provider_call(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("MonkeyCode" * 200, encoding="utf-8")
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=tool_call("read_file", {"path": "README.md"}),
                ),
                StreamEvent(type="done"),
            ],
            [StreamEvent(type="text_delta", text="Done."), StreamEvent(type="done")],
        ]
    )
    runner = AgentRunner(
        provider,
        tool_executor=create_default_executor(tmp_path),
        context_config=ContextConfig(
            context_window_tokens=32000,
            single_tool_result_tokens=10,
            turn_tool_results_tokens=20,
        ),
    )

    events = list(runner.run_turn("read", ChatSession()))

    tool_history = provider.calls[1][0][-1]
    payload = json.loads(tool_history.content)
    assert payload["archived"] is True
    assert (tmp_path / payload["archive_path"]).exists()
    assert any(event.type == "context" for event in events)


def test_agent_runner_records_provider_usage() -> None:
    provider = ScriptedProvider(
        [[StreamEvent(type="usage", usage={"prompt_tokens": 77}), StreamEvent(type="done")]]
    )
    runner = AgentRunner(provider)

    list(runner.run_turn("hi", ChatSession()))

    assert runner.context_manager.estimator._anchor_tokens == 77


def test_agent_runner_injects_instructions_and_memory(tmp_path: Path) -> None:
    (tmp_path / "MONKEYCODE.md").write_text("project rule", encoding="utf-8")
    store = MemoryStore(tmp_path, tmp_path / "home")
    store.write_note(scope="user", category="preference", content="偏好中文回答", source_session="s")
    provider = PromptCaptureProvider()
    runner = AgentRunner(
        provider,
        tool_executor=create_default_executor(tmp_path),
        memory_store=store,
        user_home=tmp_path / "home",
    )

    list(runner.run_turn("hello", ChatSession()))

    prompt = provider.calls[0]["stable_system_text"]
    assert "project rule" in prompt
    assert "偏好中文回答" in prompt
    assert "current user input wins" in prompt


def test_agent_runner_injects_skill_catalog_and_active_block(tmp_path: Path) -> None:
    catalog = Catalog()
    catalog.register(
        Skill(
            meta=SkillMeta(name="commit", description="Commit changes"),
            prompt_body="Commit body",
            source_dir=tmp_path,
            source=SkillSource.PROJECT,
        )
    )
    active = ActiveSkills()
    active.activate("commit", "Commit body")
    provider = PromptCaptureProvider()
    runner = AgentRunner(
        provider,
        tool_executor=create_default_executor(tmp_path),
        skill_catalog=catalog,
        active_skills=active,
    )

    list(runner.run_turn("hello", ChatSession()))

    assert "## Available Skills" in provider.calls[0]["stable_system_text"]
    assert "- commit: Commit changes" in provider.calls[0]["stable_system_text"]
    assert "## Active Skills" in provider.calls[0]["dynamic_system_messages"][0]
    assert "Commit body" in provider.calls[0]["dynamic_system_messages"][0]


def test_agent_runner_appends_jsonl_and_schedules_memory(tmp_path: Path) -> None:
    archive = SessionArchive.create(tmp_path)
    memory = RecordingMemoryStore()
    provider = ScriptedProvider([[StreamEvent(type="text_delta", text="Hi"), StreamEvent(type="done")]])
    runner = AgentRunner(
        provider,
        tool_executor=create_default_executor(tmp_path),
        session_archive=archive,
        memory_store=memory,
    )

    list(runner.run_turn("hello", ChatSession()))

    events = [json.loads(line)["type"] for line in archive.path.read_text(encoding="utf-8").splitlines()]
    assert "user_message" in events
    assert "assistant_message" in events
    assert len(memory.snapshots) == 1


def test_agent_runner_continues_after_tool_failure(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=tool_call("read_file", {"path": "missing.txt"}),
                ),
                StreamEvent(type="done"),
            ],
            [StreamEvent(type="text_delta", text="File missing."), StreamEvent(type="done")],
        ]
    )
    runner = AgentRunner(provider, tool_executor=create_default_executor(tmp_path))

    events = list(runner.run_turn("read missing", ChatSession()))

    assert any(
        event.type == "tool_result" and event.tool_result and event.tool_result.error_type == "file_not_found"
        for event in events
    )
    assert provider.calls == provider.calls[:2]
    assert events[-1].stop_reason == "model_done"


def test_agent_runner_stops_at_iteration_limit(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=tool_call("find_files", {"pattern": "*.py"}, ident=f"call_{index}"),
                ),
                StreamEvent(type="done"),
            ]
            for index in range(3)
        ]
    )
    runner = AgentRunner(
        provider,
        tool_executor=create_default_executor(tmp_path),
        config=AgentConfig(max_iterations=3),
    )

    events = list(runner.run_turn("loop", ChatSession()))

    assert len(provider.calls) == 3
    assert events[-1].stop_reason == "max_iterations"


def test_agent_runner_stops_after_unknown_tool_limit(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=tool_call("missing_tool", {}, ident=f"call_{index}"),
                ),
                StreamEvent(type="done"),
            ]
            for index in range(2)
        ]
    )
    runner = AgentRunner(
        provider,
        tool_executor=create_default_executor(tmp_path),
        config=AgentConfig(max_consecutive_unknown_tools=2),
    )

    events = list(runner.run_turn("unknown", ChatSession()))

    assert events[-1].stop_reason == "unknown_tool_limit"


def test_agent_runner_reports_provider_errors() -> None:
    provider = ScriptedProvider([RuntimeError("stream exploded")])
    runner = AgentRunner(provider)

    events = list(runner.run_turn("hi", ChatSession()))

    assert events[-1].type == "error"
    assert events[-1].error_message == "stream exploded"


def test_agent_runner_honors_cancelled_token() -> None:
    token = CancellationToken()
    token.cancel()
    provider = ScriptedProvider([])
    runner = AgentRunner(provider)

    events = list(runner.run_turn("hi", ChatSession(), cancel_token=token))

    assert events[-1].type == "cancelled"
    assert provider.calls == []
