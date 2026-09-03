import asyncio

from monkeycode import prompts
from monkeycode.command import NopUI, Registry, register_builtins
from monkeycode.command.builtin_ui import handle_compact
from monkeycode.command.builtin_prompt import handle_do
from monkeycode.command.builtin_local import handle_status
from monkeycode.events import AgentMode


class RecordingUI(NopUI):
    def __init__(self) -> None:
        super().__init__()
        self.set_modes: list[AgentMode] = []

    def set_mode(self, mode: AgentMode) -> None:
        super().set_mode(mode)
        self.set_modes.append(mode)


def test_register_builtins_all_registered() -> None:
    registry = Registry()
    register_builtins(registry)

    assert [command.name for command in registry.visible()] == [
        "clear",
        "compact",
        "do",
        "exit",
        "help",
        "memory",
        "permission",
        "plan",
        "resume",
        "session",
        "skill",
        "status",
    ]


def test_register_builtins_no_collision() -> None:
    registry = Registry()

    register_builtins(registry)


def test_register_builtins_handlers_run_on_nop_ui() -> None:
    registry = Registry()
    register_builtins(registry)
    ui = NopUI()

    for command in registry.visible():
        asyncio.run(command.handler(ui))


def test_handle_status_prints_all_keys() -> None:
    ui = RecordingUI()

    asyncio.run(handle_status(ui))

    text = "\n".join(ui.messages)
    for key in ["Mode", "Tokens", "Tools", "Memories", "Model", "Directory"]:
        assert key in text


def test_handle_compact_blocks_when_busy() -> None:
    ui = RecordingUI()
    ui.idle_value = False

    asyncio.run(handle_compact(ui))

    assert ui.errors == ["请等待当前任务完成"]
    assert ui.compact_called is False


def test_handle_do_sets_mode_and_injects() -> None:
    ui = RecordingUI()

    asyncio.run(handle_do(ui))

    assert ui.set_modes == [AgentMode.EXECUTE]
    assert ui.injected == [("/do", prompts.EXECUTE_DIRECTIVE)]
