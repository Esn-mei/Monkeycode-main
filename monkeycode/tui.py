from __future__ import annotations

import asyncio
import random
import re
import shutil
import sys
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from typing import TYPE_CHECKING, TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.mouse import load_mouse_bindings
from prompt_toolkit.layout.containers import HSplit, VerticalAlign, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout import DynamicContainer
from prompt_toolkit.keys import Keys
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style

from monkeycode.agent import AgentRunner
from monkeycode.command import CompletionMenu, Kind, Registry, SkillSummary, parse, register_builtins, register_skills_as_commands
from monkeycode.config import AppConfig
from monkeycode.context import ContextStatus
from monkeycode.events import AgentConfig, AgentEvent, AgentMode, CancellationToken
from monkeycode.errors import MonkeyCodeError
from monkeycode.memory import MemoryStore
from monkeycode.permissions import HumanDecision, PermissionDecision, PermissionMode, PermissionRequest
from monkeycode import prompts
from monkeycode.plan import DefaultPlanManager, PlanDocument, PlanStatus, PlanStep, parse_plan
from monkeycode.providers.base import ChatProvider
from monkeycode.session import ChatSession
from monkeycode.session_archive import SessionArchive, cleanup_expired_sessions
from monkeycode.skills import ActiveSkills, Catalog
from monkeycode.skills.executor import Executor as SkillExecutor
from monkeycode.tools.install_skill import InstallSkillTool
from monkeycode.tools.load_skill import LoadSkillTool
from monkeycode.tools.executor import ToolExecutor

if TYPE_CHECKING:
    from monkeycode.hooks.engine import HookEngine

PLAIN_EXIT_COMMANDS = {"exit", "quit"}
SHIFT_TAB_SEQUENCE = "\x1b[Z"
PERMISSION_TOGGLE_SEQUENCES = {SHIFT_TAB_SEQUENCE, "\t"}
PERMISSION_MODE_LABELS = {
    PermissionMode.DEFAULT: "Default permissions",
    PermissionMode.STRICT: "Auto-review",
    PermissionMode.ALLOW: "Full access",
}
PERMISSION_MODE_CYCLE = (PermissionMode.DEFAULT, PermissionMode.STRICT, PermissionMode.ALLOW)
POWERSHELL_MOJIBAKE_EXIT_COMMANDS = {"\u9518\u7e2cuit"}
ORANGE = "\033[38;5;208m"
DIM = "\033[2m"
RESET = "\033[0m"
BLUE = "\033[38;5;39m"
STATUS_GRAY = "\033[90m"
STATUS_INDENT = "  "
CLEAR_TERMINAL_VIEW = "\033[2J\033[3J\033[H"
ACCENT_CODES = [202, 208, 214, 179, 141, 111, 79, 203]
FIXED_FOOTER_ROWS = 4
INPUT_PLACEHOLDER = "Send a message..."
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


@dataclass
class LoopState:
    config: AppConfig
    provider: ChatProvider
    tool_executor: ToolExecutor | None
    root: Path
    enable_local_state: bool
    session: ChatSession
    archive: SessionArchive | None
    memory_store: MemoryStore | None
    catalog: Catalog
    active_skills: ActiveSkills
    skill_executor: SkillExecutor
    hook_engine: HookEngine | None
    agent: AgentRunner
    task_mgr: Any | None = None
    subagent_catalog: Any | None = None
    agent_tool: Any | None = None
    plan: PlanDocument | None = None
    mode: AgentMode = AgentMode.EXECUTE
    usage_in: int = 0
    usage_out: int = 0
    usage_total: int = 0
    quit_requested: bool = False
    pending_prompt: tuple[str, str] | None = None


@dataclass
class ConsoleFooterState:
    input_rows: int = 1
    terminal_columns: int = 0
    terminal_lines: int = 0


def _visible_len(text: str) -> int:
    length = 0
    in_escape = False
    for char in text:
        if char == "\033":
            in_escape = True
            continue
        if in_escape:
            if char == "m":
                in_escape = False
            continue
        length += _console_cell_width(char)
    return length


def _console_cell_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.category(char)[0] == "C":
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


def _center(content: str, width: int) -> str:
    available = width - 2
    padding = max(0, available - _visible_len(content))
    left = padding // 2
    right = padding - left
    return f"|{' ' * left}{content}{' ' * right}|"


def _pixel_monkey(accent_code: int, *, solid_pixels: bool = False) -> list[str]:
    pixel = (
        f"\033[38;5;{accent_code}m██{RESET}"
        if solid_pixels
        else f"\033[48;5;{accent_code}m  {RESET}"
    )
    blank = "  "
    patterns = [
        "00111100",
        "01111110",
        "11011011",
        "11111111",
        "10111101",
        "00100100",
    ]
    return ["".join(pixel if cell == "1" else blank for cell in row) for row in patterns]


def render_startup_banner(
    config: AppConfig,
    *,
    cwd: str | None = None,
    accent_code: int | None = None,
    solid_pixels: bool = False,
) -> str:
    current_dir = cwd or str(Path.cwd())
    selected_accent = accent_code or random.choice(ACCENT_CODES)
    accent = f"\033[38;5;{selected_accent}m"
    monkey_icon = _pixel_monkey(selected_accent, solid_pixels=solid_pixels)
    width = 70
    title = f" {accent}MonkeyCode{RESET} {DIM}v0.1.0{RESET} "
    title_width = _visible_len(title)
    top = f"+--{title}{'-' * max(0, width - title_width - 4)}+"
    bottom = f"+{'-' * (width - 2)}+"
    lines = [
        f"{accent}{top}{RESET}",
        _center("Welcome back!", width),
        _center("", width),
        *[_center(f"{accent}{line}{RESET}", width) for line in monkey_icon],
        _center("", width),
        _center(f"{DIM}{config.model} - {config.protocol}{RESET}", width),
        _center(f"{DIM}{current_dir}{RESET}", width),
        f"{accent}{bottom}{RESET}",
        "",
    ]
    return "\n".join(lines)


def _write_text(stdout: TextIO, text: str) -> None:
    try:
        stdout.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stdout, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        stdout.write(safe_text)


class TuiPermissionPrompter:
    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        self.stdin = stdin
        self.stdout = stdout

    def prompt(self, request: PermissionRequest, decision: PermissionDecision) -> HumanDecision:
        _write_text(self.stdout, "\n[permission] 需要确认工具调用\n")
        _write_text(self.stdout, f"tool: {request.tool_name}\n")
        _write_text(self.stdout, f"target: {_compact(request.target or '-')}\n")
        _write_text(self.stdout, f"mode: {request.mode.value}\n")
        _write_text(self.stdout, f"risk: {_risk_text(request)}\n")
        _write_text(self.stdout, f"reason: {decision.reason}\n")
        _write_text(self.stdout, "[y] 本次允许 / [s] 本会话允许 / [n] 拒绝 > ")
        self.stdout.flush()
        choice = self.stdin.readline()
        if choice == "":
            _write_text(self.stdout, "\n")
            self.stdout.flush()
            return HumanDecision.DENY
        normalized = choice.strip().lower()
        if normalized in {"y", "yes"}:
            return HumanDecision.ALLOW_ONCE
        if normalized in {"s", "session"}:
            return HumanDecision.ALLOW_SESSION
        return HumanDecision.DENY


# 流式文本渲染函数：检测 ● 前缀并套用蓝色渲染 + 代码块边框
def _write_text_stream(stdout: TextIO, text: str) -> None:
    lines = text.split(chr(10))
    in_code_block = getattr(_write_text_stream, '_in_code_block', False)
    code_block_buffer = getattr(_write_text_stream, '_code_block_buffer', [])
    for i, line in enumerate(lines):
        if i > 0:
            _write_text(stdout, chr(10))
            stdout.flush()
        stripped = line.lstrip()
        if stripped in ('[/代码]', '[/code]'):
            if code_block_buffer:
                _write_text(stdout, BLUE + chr(32) + chr(0x250c) + ' code ' + chr(0x2500) * 58 + RESET)
                for buf_line in code_block_buffer:
                    _write_text(stdout, chr(10) + BLUE + chr(32) + chr(0x2502) + chr(32) + buf_line + RESET)
                _write_text(stdout, chr(10) + BLUE + chr(32) + chr(0x2514) + chr(0x2500) * 64 + RESET)
                code_block_buffer.clear()
            _write_text_stream._in_code_block = False
            _write_text_stream._code_block_buffer = []
            stdout.flush()
            continue
        if stripped in ('[代码]', '[code]'):
            _write_text_stream._in_code_block = True
            _write_text_stream._code_block_buffer = []
            continue
        if in_code_block:
            code_block_buffer.append(line)
            _write_text_stream._code_block_buffer = code_block_buffer
        elif stripped.startswith('●') and len(stripped) > 3 and stripped[3] in (chr(32), chr(9)):
            _write_text(stdout, f'{BLUE}{line}{RESET}')
        else:
            _write_text(stdout, line)
    if not text.endswith(chr(10)):
        stdout.flush()


def _write_status_line(stdout: TextIO, text: str, *, before: bool = False) -> None:
    prefix = "\n" if before else ""
    _write_text(stdout, f"{prefix}{STATUS_INDENT}{STATUS_GRAY}{text}{RESET}\n")


def _clear_terminal_view(stdout: TextIO) -> None:
    _write_text(stdout, CLEAR_TERMINAL_VIEW)
    stdout.flush()


class LoopCommandUI:
    def __init__(self, state: LoopState, stdin: TextIO, stdout: TextIO) -> None:
        self.state = state
        self.stdin = stdin
        self.stdout = stdout

    def println(self, msg: str) -> None:
        _write_text(self.stdout, f"{msg}\n")
        self.stdout.flush()

    def error(self, msg: str) -> None:
        _write_text(self.stdout, f"Error: {msg}\n")
        self.stdout.flush()

    def mode(self) -> AgentMode:
        return self.state.mode

    def set_mode(self, mode: AgentMode) -> None:
        self.state.mode = mode
        _consume_event(AgentEvent(type="mode_changed", mode=mode), self.stdout)

    def inject_and_send(self, label: str, preset: str) -> None:
        self.state.pending_prompt = (label, preset)

    def usage_in(self) -> int:
        return self.state.usage_in

    def usage_out(self) -> int:
        return self.state.usage_out

    def usage_total(self) -> int:
        return self.state.usage_total

    def model_name(self) -> str:
        return self.state.config.model

    def cwd(self) -> str:
        return str(self.state.root)

    def tool_count(self) -> int:
        if self.state.tool_executor is None:
            return 0
        return self.state.tool_executor.registry.count()

    def memory_files(self) -> list[str]:
        if self.state.memory_store is None:
            return []
        project_files, user_files = self.state.memory_store.list_files()
        return [*project_files, *user_files]

    def session_path(self) -> str:
        return str(self.state.archive.path) if self.state.archive is not None else ""

    def session_id(self) -> str:
        return self.state.archive.session_id if self.state.archive is not None else ""

    def quit(self) -> None:
        if self.state.archive is not None:
            self.state.archive.end()
        _dispatch_loop_hook(self.state, "session.ended")
        self.state.quit_requested = True

    def force_compact(self) -> None:
        status = self.state.agent.compact_now(self.state.session)
        _write_context_status(status, self.stdout, manual=True)

    def open_resume_menu(self) -> None:
        current_session_id = self.session_id()
        summaries = [
            summary
            for summary in SessionArchive.list_summaries(self.state.root)
            if summary.session_id != current_session_id
        ]
        if not summaries:
            self.println("没有可恢复的会话")
            return
        for index, summary in enumerate(summaries, start=1):
            title = summary.title or summary.session_id
            _write_text(
                self.stdout,
                f"{index}. {summary.session_id} messages={summary.message_count} {title}\n",
            )
        _write_text(self.stdout, "选择 session 编号 > ")
        self.stdout.flush()
        choice = self.stdin.readline().strip()
        if not choice.isdigit():
            self.error("无效的 session 编号")
            return
        selected_index = int(choice) - 1
        if selected_index < 0 or selected_index >= len(summaries):
            self.error("无效的 session 编号")
            return
        self._restore_session(summaries[selected_index].session_id)

    def clear_and_new_session(self) -> None:
        if self.state.archive is not None:
            self.state.archive.end()
        self.state.session = ChatSession()
        self.state.plan = None
        self.state.archive = (
            SessionArchive.create(self.state.root)
            if self.state.enable_local_state
            else None
        )
        self.state.agent = _build_agent(self.state)
        self.state.usage_in = 0
        self.state.usage_out = 0
        self.state.usage_total = 0
        _dispatch_loop_hook(self.state, "session.cleared")
        _dispatch_loop_hook(self.state, "session.started")
        if isinstance(self.stdout, PersistentTui):
            self.stdout.clear_transcript()
            return
        _clear_terminal_view(self.stdout)
        _write_text(self.stdout, render_startup_banner(self.state.config))
        self.stdout.flush()

    def list_catalog_skills(self) -> list[SkillSummary]:
        return _skill_summaries(self.state.catalog)

    def list_active_skills(self) -> list[str]:
        return self.state.active_skills.names()

    def clear_active_skills(self) -> None:
        self.state.active_skills.clear()

    def append_assistant_message(self, text: str) -> None:
        self.state.session.add_assistant_message(text)
        if self.state.archive is not None:
            self.state.archive.append_assistant_message(text)

    def recent_messages(self, n: int) -> list:
        return self.state.session.messages[-n:]

    def all_messages(self) -> list:
        return self.state.session.messages

    def run_fork_skill(
        self,
        name: str,
        prompt: str,
        allowed_tools: list[str],
        fork_context: str,
        model: str | None,
    ) -> str:
        fork_session = ChatSession()
        if fork_context == "recent":
            fork_session.replace_messages(self.recent_messages(5))
        elif fork_context == "full":
            fork_session.replace_messages(self.all_messages())
        if model:
            _write_text(self.stdout, f"[skill] model override {model} is not supported in this runtime; using current model\n")
            self.stdout.flush()
        fork_runner = AgentRunner(
            self.state.provider,
            tool_executor=self.state.tool_executor,
            config=AgentConfig(
                default_tool_timeout_seconds=(
                    self.state.tool_executor.context.default_timeout_seconds
                    if self.state.tool_executor
                    else 10.0
                ),
                max_output_chars=(
                    self.state.tool_executor.context.max_output_chars
                    if self.state.tool_executor
                    else 12000
                ),
            ),
            context_config=self.state.config.context,
            skill_catalog=self.state.catalog,
            active_skills=ActiveSkills(),
            allowed_tool_names=allowed_tools or None,
            hook_engine=None,
        )
        try:
            for event in fork_runner.run_turn(
                prompt,
                fork_session,
                mode=AgentMode.EXECUTE,
                cancel_token=CancellationToken(),
            ):
                if event.type == "usage":
                    _record_usage(self.state, event.usage)
                    self.state.agent.context_manager.record_usage(event.usage)
                _consume_event(event, self.stdout)
        except BaseException as exc:
            return f"[skill {name} failed: {exc}]"

        for message in reversed(fork_session.messages):
            if message.role == "assistant" and isinstance(message.content, str):
                return message.content
        return f"[skill {name} failed: no assistant message]"

    def has_catalog_skill(self, name: str) -> bool:
        return self.state.catalog.get(name) is not None

    async def execute_catalog_skill(self, name: str) -> None:
        await self.state.skill_executor.execute(self, name, "")

    def cancel(self) -> None:
        _consume_event(AgentEvent(type="cancelled", stop_reason="cancelled"), self.stdout)

    def idle(self) -> bool:
        return True

    def _restore_session(self, session_id: str) -> None:
        if self.state.archive is not None and self.state.archive.session_id != session_id:
            self.state.archive.end()
        archive = SessionArchive.open(self.state.root, session_id)
        restore = archive.restore()
        self.state.session = ChatSession()
        if restore.messages:
            self.state.session.replace_messages(restore.messages)
        self.state.archive = archive
        self.state.plan = restore.plan
        self.state.agent = _build_agent(self.state)
        if self.state.plan is not None:
            self.println(_format_plan(self.state.plan))
        if restore.messages:
            status = self.state.agent.compact_now(self.state.session)
            if status.changed or status.error_message:
                _write_context_status(status, self.stdout, manual=False)
        parts = [f"session={archive.session_id}"]
        if restore.diagnostics:
            parts.append(f"restore_diagnostics={len(restore.diagnostics)}")
        if restore.skipped_bad_lines:
            parts.append(f"skipped_bad_lines={restore.skipped_bad_lines}")
        if restore.truncated_incomplete_tail:
            parts.append("truncated_incomplete_tail")
        self.println(f"[session] {' '.join(parts)}")
        _dispatch_loop_hook(self.state, "session.resumed")


def _build_agent(state: LoopState) -> AgentRunner:
    runner = AgentRunner(
        state.provider,
        tool_executor=state.tool_executor,
        config=AgentConfig(
            default_tool_timeout_seconds=(
                state.tool_executor.context.default_timeout_seconds if state.tool_executor else 10.0
            ),
            max_output_chars=(state.tool_executor.context.max_output_chars if state.tool_executor else 12000),
        ),
        context_config=state.config.context,
        session_archive=state.archive,
        memory_store=state.memory_store,
        skill_catalog=state.catalog,
        active_skills=state.active_skills,
        hook_engine=state.hook_engine,
        active_plan=state.plan,
    )
    if state.agent_tool is not None:
        state.agent_tool.set_parent(runner)
    return runner


def _dispatch_loop_hook(state: LoopState, event_name: str) -> None:
    if state.hook_engine is None:
        return
    state.hook_engine.dispatch(
        event_name,
        {
            "workspace_root": str(state.root),
            "cwd": str(Path.cwd()),
            "mode": state.mode.value,
            "session": {
                "id": state.archive.session_id if state.archive is not None else "",
                "message_count": len(state.session.messages),
            },
        },
    )


def run_chat_loop(
    config: AppConfig,
    provider: ChatProvider,
    *,
    tool_executor: ToolExecutor | None = None,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    workspace_root: Path | None = None,
    resume_session_id: str | None = None,
    enable_local_state: bool = False,
    skill_catalog: Catalog | None = None,
    active_skills: ActiveSkills | None = None,
    hook_engine: HookEngine | None = None,
    task_mgr: Any | None = None,
    subagent_catalog: Any | None = None,
    agent_tool: Any | None = None,
) -> int:
    root = (workspace_root or Path.cwd()).resolve()
    catalog = skill_catalog or Catalog.load(root)
    active = active_skills or ActiveSkills()
    skill_executor = SkillExecutor(catalog)
    if tool_executor is not None:
        _register_skill_tools(tool_executor, catalog, active, root)
    command_registry = Registry()
    register_builtins(command_registry)
    _remove_conflicting_skills(catalog, command_registry)
    register_skills_as_commands(command_registry, _skill_summaries(catalog), skill_executor)
    session = ChatSession()
    archive: SessionArchive | None = None
    restored_plan: PlanDocument | None = None
    memory_store: MemoryStore | None = None
    if enable_local_state:
        cleanup_expired_sessions(root)
        memory_store = MemoryStore(root)
        if resume_session_id:
            archive = SessionArchive.open(root, resume_session_id)
            restore = archive.restore()
            restored_plan = restore.plan
            if restore.messages:
                session.replace_messages(restore.messages)
        else:
            archive = SessionArchive.create(root)
    state = LoopState(
        config=config,
        provider=provider,
        tool_executor=tool_executor,
        root=root,
        enable_local_state=enable_local_state,
        session=session,
        archive=archive,
        memory_store=memory_store,
        catalog=catalog,
        active_skills=active,
        skill_executor=skill_executor,
        hook_engine=hook_engine,
        task_mgr=task_mgr,
        subagent_catalog=subagent_catalog,
        agent_tool=agent_tool,
        agent=None,  # type: ignore[arg-type]
        plan=restored_plan,
    )
    state.agent = _build_agent(state)
    if enable_local_state and resume_session_id and session.messages:
        state.agent.compact_now(state.session)
        _dispatch_loop_hook(state, "session.resumed")
    else:
        _dispatch_loop_hook(state, "session.started")
    fixed_footer = _should_use_fixed_footer(stdin, stdout)
    persistent_mode = fixed_footer and stdin is sys.stdin and stdout is sys.stdout
    persistent_tui: PersistentTui | None = None
    if persistent_mode:
        persistent_tui = PersistentTui(
            config,
            tool_executor,
            command_registry,
            mode_getter=lambda: state.mode,
            initial_transcript=_render_session_transcript(state.session),
        )
        persistent_tui.start()
        stdin = persistent_tui
        stdout = persistent_tui
    else:
        if fixed_footer:
            _clear_terminal_view(stdout)
        _write_text(stdout, render_startup_banner(config))
        stdout.flush()
    if tool_executor is not None:
        tool_executor.permission_manager.set_prompter(TuiPermissionPrompter(stdin, stdout))
    command_ui = LoopCommandUI(state, stdin, stdout)
    try:
        return _run_chat_interaction(
            state,
            command_ui,
            command_registry,
            stdin,
            stdout,
            fixed_footer=fixed_footer,
        )
    finally:
        if persistent_tui is not None:
            persistent_tui.close()


def _run_chat_interaction(
    state: LoopState,
    command_ui: LoopCommandUI,
    command_registry: Registry,
    stdin: TextIO,
    stdout: TextIO,
    *,
    fixed_footer: bool,
) -> int:
    while True:
        _consume_task_notifications(state, stdout)
        if not fixed_footer:
            _write_text(stdout, _prompt_for_mode(state.mode, state.tool_executor))
            stdout.flush()
        user_input = _read_user_input(
            stdin,
            stdout,
            state.mode,
            state.tool_executor,
            command_registry,
            model_name=state.config.model,
            fixed_footer=fixed_footer,
        )
        if user_input == "":
            if state.archive is not None:
                state.archive.end()
            _dispatch_loop_hook(state, "session.ended")
            if not fixed_footer:
                _write_text(stdout, "\n")
            stdout.flush()
            return 0

        raw_content = user_input.lstrip("\ufeff").rstrip("\r\n")
        if raw_content in PERMISSION_TOGGLE_SEQUENCES:
            _write_permission_toggle(stdout, state.tool_executor)
            continue

        content = raw_content.strip()
        if content in POWERSHELL_MOJIBAKE_EXIT_COMMANDS:
            content = "quit"
        if not content:
            continue
        command = content.lower()
        if command in PLAIN_EXIT_COMMANDS:
            if state.archive is not None:
                state.archive.end()
            _dispatch_loop_hook(state, "session.ended")
            return 0

        if _dispatch_slash(command, command_registry, command_ui):
            if state.quit_requested:
                return 0
            if state.pending_prompt is not None:
                _, prompt_text = state.pending_prompt
                state.pending_prompt = None
                _run_turn_and_maybe_confirm_plan(state, prompt_text, stdout, stdin)
            _write_text(stdout, "\n")
            stdout.flush()
            continue

        _run_turn_and_maybe_confirm_plan(state, content, stdout, stdin)
        _write_text(stdout, "\n")
        stdout.flush()


def _consume_task_notifications(state: LoopState, stdout: TextIO) -> None:
    if state.task_mgr is None:
        return
    queue = state.task_mgr.subscribe_done()
    while True:
        try:
            task_id = queue.get_nowait()
        except Empty:
            return
        task = state.task_mgr.get(task_id)
        if task is None:
            continue
        text = _build_task_notification(task)
        state.session.add_user_message(text)
        if state.archive is not None:
            state.archive.append_user_message(text)
        _write_text(stdout, f"\n{text}\n")
        stdout.flush()


def _build_task_notification(task) -> str:
    if task.err:
        result = f"Error: {task.err}"
    else:
        result = task.result
    return "\n".join(
        [
            "<task-notification>",
            f'Task {task.id} (name="{task.name}"): {task.status}',
            f"Result: {result}",
            "</task-notification>",
        ]
    )


def _dispatch_slash(text: str, registry: Registry, ui: LoopCommandUI) -> bool:
    name, is_slash = parse(text)
    if not is_slash:
        return False
    command = registry.lookup(name)
    if command is None:
        if name and ui.has_catalog_skill(name):
            if not ui.idle():
                ui.error("请等待当前任务完成")
                return True
            try:
                asyncio.run(ui.execute_catalog_skill(name))
            except Exception as exc:
                ui.error(str(exc))
            return True
        ui.error("未知命令: 输入 /help 查看可用命令")
        return True
    if command.kind in {Kind.UI, Kind.PROMPT} and not ui.idle():
        ui.error("请等待当前任务完成")
        return True
    try:
        asyncio.run(command.handler(ui))
    except Exception as exc:
        ui.error(str(exc))
    return True


def _run_turn_and_maybe_confirm_plan(
    state: LoopState,
    content: str,
    stdout: TextIO,
    stdin: TextIO,
) -> None:
    stop_reason = _run_agent_turn(state, content, stdout)
    if state.mode == AgentMode.PLAN and stop_reason == "model_done":
        plan = _plan_from_latest_response(state)
        if plan is None:
            _write_text(stdout, "[plan] 无法解析结构化步骤，请使用编号或项目符号重新描述计划\n")
            stdout.flush()
            return
        state.plan = plan
        state.agent.set_plan(plan)
        if state.archive is not None:
            state.archive.append_plan_created(plan)
        _write_text(stdout, _format_plan(plan) + "\n")
        if _confirm_plan(stdout, stdin):
            state.mode = AgentMode.EXECUTE
            _consume_event(AgentEvent(type="mode_changed", mode=state.mode), stdout)
            _write_text(stdout, "\n")
            stdout.flush()
            _run_agent_turn(state, prompts.EXECUTE_DIRECTIVE, stdout)
        else:
            _write_text(stdout, "[plan] 未确认，保持计划模式\n")
            stdout.flush()
        return

    if state.mode == AgentMode.EXECUTE:
        replan_prompt = state.agent.replan_prompt()
        if replan_prompt is not None:
            state.mode = AgentMode.PLAN
            _consume_event(AgentEvent(type="mode_changed", mode=state.mode), stdout)
            _run_agent_turn(state, replan_prompt, stdout)
            replacement = _plan_from_latest_response(state)
            if replacement is None:
                _write_text(stdout, "[plan] 重规划结果无法解析，保持失败状态\n")
                stdout.flush()
                return
            current_plan = state.agent.get_plan()
            if current_plan is None:
                _write_text(stdout, "[plan] 当前计划已丢失，保持失败状态\n")
                stdout.flush()
                return
            try:
                plan = state.agent.plan_manager.apply_replan(current_plan, _plan_text(replacement))
            except (TypeError, ValueError):
                _write_text(stdout, "[plan] 重规划结果无效，保持失败状态\n")
                stdout.flush()
                return
            state.plan = plan
            state.agent.set_plan(plan)
            if state.archive is not None:
                state.archive.append_plan_replanned(plan)
            _write_text(stdout, _format_plan(plan) + "\n")
            if _confirm_plan(stdout, stdin):
                state.mode = AgentMode.EXECUTE
                _consume_event(AgentEvent(type="mode_changed", mode=state.mode), stdout)
                _run_agent_turn(state, prompts.EXECUTE_DIRECTIVE, stdout)
            else:
                _write_text(stdout, "[plan] 重规划未确认，保持计划模式\n")
            stdout.flush()


def _run_agent_turn(state: LoopState, content: str, stdout: TextIO) -> str | None:
    stop_reason = None
    try:
        for event in state.agent.run_turn(
            content,
            state.session,
            mode=state.mode,
            cancel_token=CancellationToken(),
        ):
            if event.type == "usage":
                _record_usage(state, event.usage)
            if event.type == "done":
                stop_reason = event.stop_reason
            _consume_event(event, stdout)
    except MonkeyCodeError as exc:
        _write_text(stdout, f"\nError: {exc}\n")
        stdout.flush()
    return stop_reason


def _record_usage(state: LoopState, usage: dict | None) -> None:
    if not isinstance(usage, dict):
        return
    input_tokens = _first_int(usage, ("prompt_tokens", "input_tokens"))
    output_tokens = _first_int(usage, ("completion_tokens", "output_tokens"))
    total_tokens = _first_int(usage, ("total_tokens",))
    if input_tokens is not None:
        state.usage_in += input_tokens
    if output_tokens is not None:
        state.usage_out += output_tokens
    if total_tokens is not None:
        state.usage_total += total_tokens
    elif input_tokens is not None or output_tokens is not None:
        state.usage_total += (input_tokens or 0) + (output_tokens or 0)


def _skill_summaries(catalog: Catalog) -> list[SkillSummary]:
    return [
        SkillSummary(
            name=skill.meta.name,
            description=skill.meta.description,
            source=str(skill.source),
            mode=skill.meta.mode,
        )
        for skill in catalog.list()
    ]


def _register_skill_tools(
    tool_executor: ToolExecutor,
    catalog: Catalog,
    active: ActiveSkills,
    root: Path,
) -> None:
    registry = tool_executor.registry
    if registry.get("load_skill") is None:
        registry.register(LoadSkillTool(catalog, active, registry))
    if registry.get("install_skill") is None:
        registry.register(InstallSkillTool(catalog, root))
    issues = catalog.validate_tools(registry)
    if issues:
        for issue in issues:
            print(
                f'skill {issue.skill_name}: allowed_tool "{issue.tool_name}" not registered, skipped',
                file=sys.stderr,
            )
        catalog.remove_many({issue.skill_name for issue in issues})


def _remove_conflicting_skills(catalog: Catalog, command_registry: Registry) -> None:
    conflicts: set[str] = set()
    for skill in catalog.list():
        if command_registry.lookup(skill.meta.name) is not None:
            print(
                f"skill {skill.meta.name}: conflicts with built-in slash command, skipped",
                file=sys.stderr,
            )
            conflicts.add(skill.meta.name)
    catalog.remove_many(conflicts)


def _first_int(data: dict, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _consume_event(event: AgentEvent, stdout: TextIO) -> None:
    if event.type == "text_delta" and event.text:
        _write_text_stream(stdout, event.text)
        stdout.flush()
        return
    if event.type == "tool_call_started" and event.tool_call:
        _write_status_line(stdout, f"[tool] {event.tool_call.name} running...", before=True)
        stdout.flush()
        return
    if event.type == "plan_step":
        plan = event.metadata.get("plan")
        if isinstance(plan, dict):
            status = plan.get("status", "unknown")
            _write_status_line(stdout, f"[plan] status={status}")
            stdout.flush()
        return
    if event.type == "tool_result" and event.tool_call and event.tool_result:
        if event.tool_result.metadata.get("deduplicated"):
            return
        status = "done" if event.tool_result.success else "failed"
        _write_status_line(stdout, f"[tool] {event.tool_call.name} {status}")
        if not event.tool_result.success and event.tool_result.error_message:
            _write_status_line(stdout, event.tool_result.error_message)
        stdout.flush()
        return
    if event.type == "mode_changed" and event.mode:
        label = "plan" if event.mode == AgentMode.PLAN else "execute"
        _write_status_line(stdout, f"[mode] {label}")
        stdout.flush()
        return
    if event.type == "cancelled":
        _write_status_line(stdout, "[agent] cancelled")
        stdout.flush()
        return
    if event.type == "usage":
        return
    if event.type == "context":
        status = event.metadata.get("context_status")
        if isinstance(status, ContextStatus):
            _write_context_status(status, stdout, manual=False)
        return
    if event.type == "error":
        _write_text(stdout, f"\nError: {event.error_message or event.error_type}\n")
        stdout.flush()
        return
    if event.type == "done" and event.stop_reason == "max_iterations":
        _write_status_line(stdout, "[agent] reached max iterations", before=True)
        stdout.flush()


def _read_user_input(
    stdin: TextIO,
    stdout: TextIO,
    mode: AgentMode,
    tool_executor: ToolExecutor | None,
    command_registry: Registry,
    *,
    model_name: str = "",
    fixed_footer: bool = False,
    footer_state: ConsoleFooterState | None = None,
    banner_text: str = "",
    transcript: str = "",
) -> str:
    if isinstance(stdin, PersistentTui):
        return stdin.readline()
    if fixed_footer and stdin is sys.stdin and stdout is sys.stdout:
        return _read_prompt_toolkit_line(
            stdout,
            mode,
            tool_executor,
            command_registry,
            model_name,
            banner_text=banner_text,
            transcript=transcript,
        )
    if _should_use_console_reader(stdin):
        return _read_console_line(
            stdout,
            mode,
            tool_executor,
            command_registry,
            model_name=model_name,
            fixed_footer=False,
        )
    if fixed_footer:
        text = stdin.readline()
        if not text:
            return ""
        committed = text.rstrip("\r\n")
        _commit_console_footer_input(
            stdout,
            committed,
            footer_state=footer_state,
        )
        return committed + "\n"
    return stdin.readline()


class _SlashCommandCompleter(Completer):
    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or any(char.isspace() for char in text):
            return
        prefix = text[1:]
        for command in self.registry.prefix_match(text):
            yield Completion(
                command.name,
                start_position=-len(prefix),
                display=f"/{command.name}  {command.description}",
            )


class _SlashCommandSuggest(AutoSuggest):
    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def get_suggestion(
        self,
        buffer: Buffer,
        document: Document,
    ) -> Suggestion | None:
        text = document.text_before_cursor
        if not text.startswith("/") or any(char.isspace() for char in text):
            return None
        matches = self.registry.prefix_match(text)
        if not matches:
            return None
        completed = f"/{matches[0].name}"
        if not completed.startswith(text):
            return None
        return Suggestion(completed[len(text) :])


def _redirect_input_scroll_to_transcript(input_window: Window, transcript_window: Window) -> None:
    """Keep the input focused while making its wheel events scroll chat history."""
    original_mouse_handler = input_window._mouse_handler

    def mouse_handler(mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            transcript_window._scroll_up()
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            transcript_window._scroll_down()
            return None
        return original_mouse_handler(mouse_event)

    input_window._mouse_handler = mouse_handler


def _vt_mouse_wheel_event_type(data: str) -> MouseEventType | None:
    """Extract wheel direction from VT100, SGR, or urxvt mouse packets."""
    try:
        if data.startswith("\x1b[<"):
            code = int(data[3:].split(";", 1)[0])
        elif len(data) >= 4 and data[2] == "M":
            code = ord(data[3]) - 32
        else:
            code = int(data[2:].split(";", 1)[0])
    except (IndexError, ValueError):
        return None

    if not code & 64:
        return None
    return MouseEventType.SCROLL_DOWN if code & 1 else MouseEventType.SCROLL_UP


class PersistentTui:
    """单实例全屏 TUI：固定顶部、可伸缩对话区和固定底部输入区。"""

    encoding = "utf-8"

    def __init__(
        self,
        config: AppConfig,
        tool_executor: ToolExecutor | None,
        command_registry: Registry,
        mode_getter,
        *,
        initial_transcript: str = "",
        prompt_input=None,
        prompt_output=None,
    ) -> None:
        self.config = config
        self.tool_executor = tool_executor
        self.command_registry = command_registry
        self.mode_getter = mode_getter
        self._prompt_input = prompt_input
        self._prompt_output = prompt_output
        self._input_queue: Queue[str] = Queue()
        self._pending_output: list[str] = []
        self._pending_lock = threading.Lock()
        self._flush_scheduled = False
        self._started = threading.Event()
        self._closed = False
        self._thread: threading.Thread | None = None
        self._transcript_buffer = Buffer(
            document=Document(
                initial_transcript,
                cursor_position=len(initial_transcript),
            ),
            read_only=True,
        )
        self._banner = render_startup_banner(
            config,
            solid_pixels=True,
        ).rstrip("\n")
        self._session = self._create_session()

    def _create_session(self) -> PromptSession[str]:
        bindings = KeyBindings()

        @bindings.add("s-tab", eager=True, save_before=lambda event: False)
        def _cycle_permissions(event) -> None:
            if self.tool_executor is not None:
                _cycle_permission_mode(self.tool_executor)

        @bindings.add(Keys.ScrollUp, eager=True)
        def _scroll_transcript_up(event) -> None:
            self._scroll_transcript(MouseEventType.SCROLL_UP)

        @bindings.add(Keys.ScrollDown, eager=True)
        def _scroll_transcript_down(event) -> None:
            self._scroll_transcript(MouseEventType.SCROLL_DOWN)

        # Windows Terminal can downgrade wheel input to plain Up/Down key
        # presses. This prompt is single-line, so reserve those keys for the
        # conversation transcript instead of recalling sent messages.
        @bindings.add(Keys.Up, eager=True)
        def _scroll_transcript_with_up(event) -> None:
            self._scroll_transcript(MouseEventType.SCROLL_UP)

        @bindings.add(Keys.Down, eager=True)
        def _scroll_transcript_with_down(event) -> None:
            self._scroll_transcript(MouseEventType.SCROLL_DOWN)

        default_vt_mouse_handler = next(
            binding.handler
            for binding in load_mouse_bindings().bindings
            if binding.keys == (Keys.Vt100MouseEvent,)
        )

        @bindings.add(Keys.Vt100MouseEvent, eager=True)
        def _route_vt_mouse_wheel(event):
            event_type = _vt_mouse_wheel_event_type(event.data)
            if event_type is not None:
                self._scroll_transcript(event_type)
                return None
            return default_vt_mouse_handler(event)

        def message() -> FormattedText:
            columns, _ = shutil.get_terminal_size(fallback=(80, 24))
            usable_columns = max(1, columns - 1)
            return FormattedText(
                [
                    ("class:separator", "-" * usable_columns),
                    ("", "\n"),
                    ("class:prompt", "› "),
                ]
            )

        def bottom_toolbar() -> FormattedText:
            columns, _ = shutil.get_terminal_size(fallback=(80, 24))
            usable_columns = max(1, columns - 1)
            permission = (
                _permission_mode_label(self.tool_executor.permission_manager.mode)
                if self.tool_executor is not None
                else "Tools disabled"
            )
            mode = self.mode_getter()
            mode_prefix = "PLAN · " if mode == AgentMode.PLAN else ""
            left = f"{mode_prefix}{permission} (shift+tab to cycle)"
            if len(left) > usable_columns:
                left = f"{mode_prefix}{permission}"
            return FormattedText(
                [
                    ("class:separator", "-" * usable_columns),
                    ("", "\n"),
                    ("class:status", left[:usable_columns]),
                ]
            )

        style = Style.from_dict(
            {
                "separator": "ansibrightblack",
                "prompt": "ansibrightcyan",
                "placeholder": "ansibrightblack",
                "status": "ansibrightblack",
                "completion-menu": "bg:#202020 #c0c0c0",
                "completion-menu.completion.current": "bg:#444444 #ffffff",
                "bottom-toolbar": "noreverse",
                "bottom-toolbar.text": "noreverse ansibrightblack",
            }
        )
        session: PromptSession[str] = PromptSession(
            message=message,
            completer=_SlashCommandCompleter(self.command_registry),
            auto_suggest=_SlashCommandSuggest(self.command_registry),
            complete_while_typing=True,
            reserve_space_for_menu=0,
            placeholder=FormattedText(
                [("class:placeholder", INPUT_PLACEHOLDER)]
            ),
            bottom_toolbar=bottom_toolbar,
            key_bindings=bindings,
            style=style,
            erase_when_done=False,
            input=self._prompt_input,
            output=self._prompt_output,
        )
        session.default_buffer.accept_handler = self._accept_input
        if isinstance(session.layout.container, HSplit):
            session.layout.container.align = VerticalAlign.BOTTOM
        session.layout.current_window.height = Dimension.exact(1)

        full_banner = Window(
            FormattedTextControl(ANSI(self._banner)),
            height=Dimension.exact(len(self._banner.splitlines())),
            wrap_lines=False,
            always_hide_cursor=True,
        )
        compact_banner = Window(
            FormattedTextControl(
                lambda: FormattedText(
                    [
                        ("class:prompt", " MonkeyCode"),
                        ("class:status", f" v0.1.0 · {self.config.model}"),
                    ]
                )
            ),
            height=Dimension.exact(1),
            always_hide_cursor=True,
        )

        def header():
            columns, lines = shutil.get_terminal_size(fallback=(80, 24))
            return full_banner if lines >= 18 and columns >= 72 else compact_banner

        transcript_window = Window(
            BufferControl(buffer=self._transcript_buffer, focusable=False),
            wrap_lines=True,
            always_hide_cursor=True,
        )
        _redirect_input_scroll_to_transcript(
            session.layout.current_window,
            transcript_window,
        )
        session.layout.container = HSplit(
            [
                DynamicContainer(header),
                transcript_window,
                session.layout.container,
            ]
        )
        # PromptSession 不公开 full_screen 构造参数；Application 和已经创建的
        # Renderer 必须同时切换，不能只改 Application 标志。
        session.app.full_screen = True
        session.app.renderer.full_screen = True
        return session

    def _scroll_transcript(self, event_type: MouseEventType) -> None:
        """Route wheel events to chat history instead of prompt history."""
        transcript_window = self._session.layout.container.children[1]
        if event_type == MouseEventType.SCROLL_UP:
            transcript_window._scroll_up()
        elif event_type == MouseEventType.SCROLL_DOWN:
            transcript_window._scroll_down()

    def _accept_input(self, buffer: Buffer) -> bool:
        text = buffer.text
        self.write(f"You > {text}\n")
        self._input_queue.put(text + "\n")
        return False

    def start(self) -> None:
        self._session.app.pre_run_callables.append(self._on_started)
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="monkeycode-tui",
        )
        self._thread.start()
        if not self._started.wait(timeout=5):
            raise RuntimeError("MonkeyCode TUI failed to start")

    def _on_started(self) -> None:
        self._started.set()
        self._drain_output()

    def _run(self) -> None:
        try:
            self._session.app.run()
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            if not self._closed:
                self._input_queue.put("")

    def readline(self, _size: int = -1) -> str:
        return self._input_queue.get()

    def write(self, text: str) -> int:
        plain = _ANSI_ESCAPE_RE.sub("", text).replace("\r", "")
        if not plain:
            return len(text)
        with self._pending_lock:
            self._pending_output.append(plain)
            if self._flush_scheduled:
                return len(text)
            self._flush_scheduled = True
        loop = self._session.app.loop
        if loop is not None:
            loop.call_soon_threadsafe(self._drain_output)
        return len(text)

    def _drain_output(self) -> None:
        with self._pending_lock:
            pending = "".join(self._pending_output)
            self._pending_output.clear()
            self._flush_scheduled = False
        if pending:
            text = self._transcript_buffer.text + pending
            self._transcript_buffer.set_document(
                Document(text, cursor_position=len(text)),
                bypass_readonly=True,
            )
        self._session.app.invalidate()

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return True

    def clear_transcript(self) -> None:
        def clear() -> None:
            self._transcript_buffer.set_document(
                Document("", cursor_position=0),
                bypass_readonly=True,
            )
            self._session.app.invalidate()

        loop = self._session.app.loop
        if loop is not None:
            loop.call_soon_threadsafe(clear)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = self._session.app.loop
        if loop is not None and self._session.app.is_running:
            loop.call_soon_threadsafe(self._session.app.exit)
        if self._thread is not None:
            self._thread.join(timeout=5)


def _read_prompt_toolkit_line(
    stdout: TextIO,
    mode: AgentMode,
    tool_executor: ToolExecutor | None,
    command_registry: Registry,
    model_name: str,
    *,
    banner_text: str = "",
    transcript: str = "",
) -> str:
    bindings = KeyBindings()

    @bindings.add("s-tab", eager=True, save_before=lambda event: False)
    def _cycle_permissions(event) -> None:
        if tool_executor is not None:
            _cycle_permission_mode(tool_executor)

    def message() -> FormattedText:
        columns, _ = shutil.get_terminal_size(fallback=(80, 24))
        usable_columns = max(1, columns - 1)
        return FormattedText(
            [
                ("class:separator", "-" * usable_columns),
                ("", "\n"),
                ("class:prompt", "› "),
            ]
        )

    def bottom_toolbar() -> FormattedText:
        columns, _ = shutil.get_terminal_size(fallback=(80, 24))
        usable_columns = max(1, columns - 1)
        permission = (
            _permission_mode_label(tool_executor.permission_manager.mode)
            if tool_executor is not None
            else "Tools disabled"
        )
        mode_prefix = "PLAN · " if mode == AgentMode.PLAN else ""
        left = f"{mode_prefix}{permission} (shift+tab to cycle)"
        if len(left) > usable_columns:
            left = f"{mode_prefix}{permission}"
        status = left[:usable_columns]
        return FormattedText(
            [
                ("class:separator", "-" * usable_columns),
                ("", "\n"),
                ("class:status", status),
            ]
        )

    style = Style.from_dict(
        {
            "separator": "ansibrightblack",
            "prompt": "ansibrightcyan",
            "placeholder": "ansibrightblack",
            "status": "ansibrightblack",
            "completion-menu": "bg:#202020 #c0c0c0",
            "completion-menu.completion.current": "bg:#444444 #ffffff",
            "bottom-toolbar": "noreverse",
            "bottom-toolbar.text": "noreverse ansibrightblack",
        }
    )
    session: PromptSession[str] = PromptSession(
        completer=_SlashCommandCompleter(command_registry),
        auto_suggest=_SlashCommandSuggest(command_registry),
        complete_while_typing=True,
        # 补全菜单使用浮层显示，不能预留固定高度，否则输入栏与状态栏之间
        # 会永久出现一大片空白。
        reserve_space_for_menu=0,
        placeholder=FormattedText(
            [("class:placeholder", INPUT_PLACEHOLDER)]
        ),
        bottom_toolbar=bottom_toolbar,
        key_bindings=bindings,
        style=style,
        erase_when_done=True,
    )
    if isinstance(session.layout.container, HSplit):
        session.layout.container.align = VerticalAlign.BOTTOM
    session.layout.current_window.height = Dimension.exact(1)
    if banner_text:
        banner = banner_text.rstrip("\n")
        banner_window = Window(
            FormattedTextControl(ANSI(banner)),
            height=Dimension.exact(len(banner.splitlines())),
            wrap_lines=False,
            always_hide_cursor=True,
        )
        transcript_buffer = Buffer(
            document=Document(transcript, cursor_position=len(transcript)),
            read_only=True,
        )
        transcript_window = Window(
            BufferControl(buffer=transcript_buffer, focusable=False),
            wrap_lines=True,
            always_hide_cursor=True,
        )
        session.layout.container = HSplit(
            [
                banner_window,
                transcript_window,
                session.layout.container,
            ]
        )
        session.app.full_screen = True
    try:
        text = session.prompt(message)
    except EOFError:
        return ""
    _write_text(stdout, f"You > {text}\n")
    stdout.flush()
    return text + "\n"


def _render_session_transcript(session: ChatSession) -> str:
    lines: list[str] = []
    for message in session.messages:
        if not isinstance(message.content, str) or not message.content:
            continue
        if message.role == "user":
            lines.append(f"You > {message.content}")
        elif message.role == "assistant":
            lines.append(message.content)
    return "\n\n".join(lines)


def _should_use_console_reader(stdin: TextIO) -> bool:
    return sys.platform == "win32" and stdin is sys.stdin and stdin.isatty()


def _should_use_fixed_footer(stdin: TextIO, stdout: TextIO) -> bool:
    return (
        _should_use_console_reader(stdin)
        and hasattr(stdout, "isatty")
        and stdout.isatty()
    )


def _read_console_line(
    stdout: TextIO,
    mode: AgentMode,
    tool_executor: ToolExecutor | None,
    command_registry: Registry,
    *,
    model_name: str = "",
    fixed_footer: bool = False,
    footer_state: ConsoleFooterState | None = None,
) -> str:
    import msvcrt

    return _read_console_line_from_source(
        msvcrt,
        stdout,
        mode,
        tool_executor,
        command_registry,
        model_name=model_name,
        fixed_footer=fixed_footer,
        footer_state=footer_state,
    )


def _read_console_line_from_source(
    msvcrt,
    stdout: TextIO,
    mode: AgentMode,
    tool_executor: ToolExecutor | None,
    command_registry: Registry,
    *,
    model_name: str = "",
    fixed_footer: bool = False,
    footer_state: ConsoleFooterState | None = None,
) -> str:

    buffer: list[str] = []
    completion = CompletionMenu()
    footer_state = footer_state or ConsoleFooterState()
    pasted_burst = False
    pasted_carriage_return = False
    while True:
        char = msvcrt.getwch()
        if char is None:
            if fixed_footer:
                _render_console_footer(
                    stdout,
                    mode,
                    tool_executor,
                    model_name,
                    buffer,
                    footer_state=footer_state,
                )
            continue
        if char in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            if completion.active and key in {"H", "P"}:
                if key == "H":
                    completion.move_up()
                else:
                    completion.move_down()
                _render_console_completion(
                    stdout,
                    mode,
                    tool_executor,
                    buffer,
                    completion,
                    model_name=model_name,
                    fixed_footer=fixed_footer,
                    footer_state=footer_state,
                )
                continue
            if key == "\x0f":
                _handle_permission_toggle(
                    stdout,
                    mode,
                    tool_executor,
                    buffer,
                    inline=True,
                    model_name=model_name,
                    fixed_footer=fixed_footer,
                    footer_state=footer_state,
                )
            continue
        if char == "\t":
            if completion.active:
                selected = completion.selected()
                if selected is not None:
                    if fixed_footer:
                        _commit_console_footer_input(
                            stdout,
                            f"/{selected.name}",
                            footer_state=footer_state,
                        )
                    return f"/{selected.name}\n"
                completion.hide()
                _render_console_completion(
                    stdout,
                    mode,
                    tool_executor,
                    buffer,
                    completion,
                    model_name=model_name,
                    fixed_footer=fixed_footer,
                    footer_state=footer_state,
                )
                continue
            _handle_permission_toggle(
                stdout,
                mode,
                tool_executor,
                buffer,
                inline=True,
                model_name=model_name,
                fixed_footer=fixed_footer,
                footer_state=footer_state,
            )
            continue
        if char == "\x1b":
            sequence = _read_console_escape_sequence(msvcrt, char)
            if sequence == SHIFT_TAB_SEQUENCE:
                _handle_permission_toggle(
                    stdout,
                    mode,
                    tool_executor,
                    buffer,
                    inline=True,
                    model_name=model_name,
                    fixed_footer=fixed_footer,
                    footer_state=footer_state,
                )
                continue
            if completion.active:
                completion.hide()
                _render_console_completion(
                    stdout,
                    mode,
                    tool_executor,
                    buffer,
                    completion,
                    model_name=model_name,
                    fixed_footer=fixed_footer,
                    footer_state=footer_state,
                )
                continue
            buffer.append(sequence)
            if fixed_footer:
                _render_console_footer(
                    stdout,
                    mode,
                    tool_executor,
                    model_name,
                    buffer,
                    footer_state=footer_state,
                )
            else:
                _write_text(stdout, sequence)
                stdout.flush()
            continue
        if char in {"\r", "\n"}:
            pending_input = _console_has_pending_input(msvcrt)
            if pasted_carriage_return and char == "\n":
                pasted_carriage_return = False
                if fixed_footer and not pending_input:
                    _render_console_footer(
                        stdout,
                        mode,
                        tool_executor,
                        model_name,
                        buffer,
                        footer_state=footer_state,
                    )
                continue
            if pending_input:
                buffer.append("\n")
                pasted_carriage_return = char == "\r"
                pasted_burst = True
                completion.hide()
                if fixed_footer:
                    if not _console_has_pending_input(msvcrt):
                        _render_console_footer(
                            stdout,
                            mode,
                            tool_executor,
                            model_name,
                            buffer,
                            footer_state=footer_state,
                        )
                else:
                    _write_text(stdout, "\n")
                    stdout.flush()
                continue
            pasted_carriage_return = False
            if completion.active:
                selected = completion.selected()
                if selected is not None:
                    if fixed_footer:
                        _commit_console_footer_input(
                            stdout,
                            f"/{selected.name}",
                            footer_state=footer_state,
                        )
                    else:
                        _write_text(stdout, "\n")
                        stdout.flush()
                    return f"/{selected.name}\n"
            text = "".join(buffer)
            if fixed_footer:
                _commit_console_footer_input(stdout, text, footer_state=footer_state)
            else:
                _write_text(stdout, "\n")
                stdout.flush()
            return text + "\n"
        pasted_carriage_return = False
        if char in {"\b", "\x7f"}:
            if buffer:
                removed = buffer.pop()
                if not fixed_footer:
                    _erase_console_input_char(stdout, removed)
                completion_was_active = completion.active
                _sync_console_completion(buffer, command_registry, completion)
                pending_input = _console_has_pending_input(msvcrt)
                if pending_input:
                    pasted_burst = True
                if (completion.active or completion_was_active) and not pending_input and not pasted_burst:
                    _render_console_completion(
                        stdout,
                        mode,
                        tool_executor,
                        buffer,
                        completion,
                        model_name=model_name,
                        fixed_footer=fixed_footer,
                        footer_state=footer_state,
                    )
                else:
                    if not pending_input:
                        pasted_burst = False
                    if fixed_footer:
                        _render_console_footer(
                            stdout,
                            mode,
                            tool_executor,
                            model_name,
                            buffer,
                            footer_state=footer_state,
                        )
                    else:
                        stdout.flush()
            continue
        if char == "\x03":
            raise KeyboardInterrupt
        if char == "\x1a":
            if fixed_footer:
                _teardown_console_footer(stdout, footer_state=footer_state)
            return ""
        buffer.append(char)
        if not fixed_footer:
            _write_text(stdout, char)
        _sync_console_completion(buffer, command_registry, completion)
        pending_input = _console_has_pending_input(msvcrt)
        if pending_input:
            pasted_burst = True
        if completion.active and not pending_input and not pasted_burst:
            _render_console_completion(
                stdout,
                mode,
                tool_executor,
                buffer,
                completion,
                model_name=model_name,
                fixed_footer=fixed_footer,
                footer_state=footer_state,
            )
        else:
            if not pending_input:
                pasted_burst = False
            if fixed_footer and not pending_input:
                _render_console_footer(
                    stdout,
                    mode,
                    tool_executor,
                    model_name,
                    buffer,
                    footer_state=footer_state,
                )
            else:
                stdout.flush()


def _erase_console_input_char(stdout: TextIO, char: str) -> None:
    width = max(1, _visible_len(char))
    _write_text(stdout, "\b \b" * width)


def _sync_console_completion(buffer: list[str], registry: Registry, completion: CompletionMenu) -> None:
    text = "".join(buffer)
    if text.startswith("/"):
        completion.update(text, registry)
    else:
        completion.hide()


def _render_console_completion(
    stdout: TextIO,
    mode: AgentMode,
    tool_executor: ToolExecutor | None,
    buffer: list[str],
    completion: CompletionMenu,
    *,
    model_name: str = "",
    fixed_footer: bool = False,
    footer_state: ConsoleFooterState | None = None,
) -> None:
    buffer_text = "".join(buffer)
    hint = _inline_completion_hint(buffer_text, completion)
    if fixed_footer:
        _render_console_footer(
            stdout,
            mode,
            tool_executor,
            model_name,
            buffer,
            hint=hint,
            footer_state=footer_state,
        )
        return
    _write_text(stdout, "\r\033[2K")
    _write_text(stdout, _prompt_for_mode(mode, tool_executor))
    _write_text(stdout, buffer_text)
    if hint:
        _write_text(stdout, f"{DIM}{hint}{RESET}")
        _write_text(stdout, "\b" * len(hint))
    stdout.flush()


def _inline_completion_hint(buffer_text: str, completion: CompletionMenu) -> str:
    if not completion.active:
        return ""
    selected = completion.selected()
    if selected is None or not buffer_text.startswith("/"):
        return ""
    typed_name = buffer_text[1:].lower()
    if not selected.name.startswith(typed_name):
        return ""
    return selected.name[len(typed_name) :]


def _console_has_pending_input(msvcrt_module) -> bool:
    kbhit = getattr(msvcrt_module, "kbhit", None)
    if kbhit is None:
        return False
    try:
        return bool(kbhit())
    except Exception:
        return False


def _read_console_escape_sequence(msvcrt_module, first: str) -> str:
    sequence = first
    deadline = time.monotonic() + 0.03
    while time.monotonic() < deadline:
        if msvcrt_module.kbhit():
            sequence += msvcrt_module.getwch()
            if sequence == SHIFT_TAB_SEQUENCE or len(sequence) >= len(SHIFT_TAB_SEQUENCE):
                break
            deadline = time.monotonic() + 0.03
            continue
        time.sleep(0.001)
    return sequence


def _handle_permission_toggle(
    stdout: TextIO,
    mode: AgentMode,
    tool_executor: ToolExecutor | None,
    buffer: list[str] | None = None,
    *,
    inline: bool = False,
    model_name: str = "",
    fixed_footer: bool = False,
    footer_state: ConsoleFooterState | None = None,
) -> None:
    if inline:
        if tool_executor is not None:
            _cycle_permission_mode(tool_executor)
        if fixed_footer:
            _render_console_footer(
                stdout,
                mode,
                tool_executor,
                model_name,
                buffer or [],
                footer_state=footer_state,
            )
            return
        _write_text(stdout, "\r\033[2K")
        _write_text(stdout, _prompt_for_mode(mode, tool_executor))
        if buffer:
            _write_text(stdout, "".join(buffer))
        stdout.flush()
        return
    _write_text(stdout, "\n")
    _write_permission_toggle(stdout, tool_executor)
    _write_text(stdout, _prompt_for_mode(mode, tool_executor))
    if buffer:
        _write_text(stdout, "".join(buffer))
    stdout.flush()


def _write_permission_toggle(stdout: TextIO, tool_executor: ToolExecutor | None) -> None:
    if tool_executor is None:
        _write_text(stdout, "[permissions] tools are not enabled\n")
    else:
        label = _cycle_permission_mode(tool_executor)
        _write_text(stdout, f"[permissions] {label}\n")
    stdout.flush()


def _cache_summary(cache_usage: object) -> str | None:
    if not isinstance(cache_usage, dict):
        return None
    if not cache_usage.get("available"):
        return None
    provider = cache_usage.get("provider") or "provider"
    read = cache_usage.get("cache_read_tokens")
    creation = cache_usage.get("cache_creation_tokens")
    cached = cache_usage.get("cached_tokens")
    if creation is not None:
        return f"{provider} read={read or 0} created={creation}"
    if cached is not None:
        return f"{provider} cached={cached}"
    return f"{provider} available"


def _write_context_status(status: ContextStatus, stdout: TextIO, *, manual: bool) -> None:
    message = _context_status_text(status, manual=manual)
    if not message:
        return
    _write_status_line(stdout, f"[context] {message}", before=True)
    stdout.flush()


def _render_console_footer(
    stdout: TextIO,
    mode: AgentMode,
    tool_executor: ToolExecutor | None,
    model_name: str,
    buffer: list[str] | None = None,
    *,
    hint: str = "",
    footer_state: ConsoleFooterState | None = None,
) -> None:
    columns, lines = shutil.get_terminal_size(fallback=(80, 24))
    state = footer_state or ConsoleFooterState()
    if lines <= FIXED_FOOTER_ROWS + 2:
        state.terminal_columns = columns
        state.terminal_lines = lines
        return
    status_row = lines
    bottom_rule_row = status_row - 1
    buffer_text = "".join(buffer or [])
    prefix = f"{BLUE}›{RESET} "
    continuation_prefix = "  "
    available = max(1, columns - _visible_len(prefix))
    logical_lines = buffer_text.split("\n") if buffer_text else [""]
    max_input_rows = max(1, lines - 5)
    shown_lines = logical_lines[-max_input_rows:]
    input_rows = len(shown_lines)
    first_input_row = bottom_rule_row - input_rows
    top_rule_row = first_input_row - 1
    content_bottom = top_rule_row - 1
    previous_top_rule_row = (
        state.terminal_lines - max(1, state.input_rows) - 2
        if state.terminal_lines
        else top_rule_row
    )

    permission = (
        _permission_mode_label(tool_executor.permission_manager.mode)
        if tool_executor is not None
        else "Tools disabled"
    )
    mode_prefix = "PLAN · " if mode == AgentMode.PLAN else ""
    left_plain = f"{mode_prefix}{permission}"
    left = f"{left_plain} {DIM}(shift+tab to cycle){RESET}"
    if _visible_len(left) > columns:
        left = left_plain
    status = left

    _write_text(stdout, f"\033[1;{content_bottom}r")
    if state.terminal_lines:
        previous_bottom = min(state.terminal_lines, lines)
        for row in range(max(1, previous_top_rule_row), previous_bottom + 1):
            _write_text(stdout, f"\033[{row};1H\033[2K")
    for row in range(top_rule_row, status_row + 1):
        _write_text(stdout, f"\033[{row};1H\033[2K")
    rule = "-" * columns
    _write_text(stdout, f"\033[{top_rule_row};1H\033[2K{DIM}{rule}{RESET}")
    visible_lines: list[str] = []
    for index, line in enumerate(shown_lines):
        visible = _tail_by_visible_width(line, available)
        visible_lines.append(visible)
        row = first_input_row + index
        line_prefix = prefix if index == 0 else continuation_prefix
        input_content = visible
        if not buffer_text and index == 0:
            input_content = f"{DIM}{INPUT_PLACEHOLDER[:available]}{RESET}"
        elif hint and index == len(shown_lines) - 1:
            remaining = max(0, available - _visible_len(visible))
            input_content += f"{DIM}{hint[:remaining]}{RESET}"
        _write_text(stdout, f"\033[{row};1H\033[2K{line_prefix}{input_content}")
    _write_text(stdout, f"\033[{bottom_rule_row};1H\033[2K{DIM}{rule}{RESET}")
    _write_text(stdout, f"\033[{status_row};1H\033[2K{DIM}{status}{RESET}")
    cursor_column = min(
        columns,
        _visible_len(continuation_prefix if input_rows > 1 else prefix)
        + _visible_len(visible_lines[-1])
        + 1,
    )
    _write_text(stdout, f"\033[{first_input_row + input_rows - 1};{cursor_column}H")
    state.input_rows = input_rows
    state.terminal_columns = columns
    state.terminal_lines = lines
    stdout.flush()


def _tail_by_visible_width(text: str, width: int) -> str:
    result: list[str] = []
    used = 0
    for char in reversed(text):
        char_width = _console_cell_width(char)
        if used + char_width > width:
            break
        result.append(char)
        used += char_width
    return "".join(reversed(result))


def _commit_console_footer_input(
    stdout: TextIO,
    text: str,
    *,
    footer_state: ConsoleFooterState | None = None,
) -> None:
    _, lines = shutil.get_terminal_size(fallback=(80, 24))
    state = footer_state or ConsoleFooterState()
    content_bottom = max(1, lines - max(1, state.input_rows) - 3)
    top_rule_row = content_bottom + 1
    for row in range(top_rule_row, lines + 1):
        _write_text(stdout, f"\033[{row};1H\033[2K")
    _write_text(stdout, f"\033[1;{content_bottom}r")
    _write_text(stdout, f"\033[{content_bottom};1H\033[2KYou > {text}\n")
    state.input_rows = 1
    stdout.flush()


def _teardown_console_footer(
    stdout: TextIO,
    *,
    footer_state: ConsoleFooterState | None = None,
) -> None:
    _, lines = shutil.get_terminal_size(fallback=(80, 24))
    state = footer_state or ConsoleFooterState()
    _write_text(stdout, "\033[r")
    top_rule_row = max(1, lines - max(1, state.input_rows) - 2)
    for row in range(top_rule_row, lines + 1):
        _write_text(stdout, f"\033[{row};1H\033[2K")
    _write_text(stdout, f"\033[{lines};1H\n")
    state.input_rows = 1
    stdout.flush()


def _context_status_text(status: ContextStatus, *, manual: bool) -> str | None:
    if not status.enabled:
        return "context management disabled" if manual else None
    parts: list[str] = []
    if status.archived_count:
        parts.append(f"archived {status.archived_count} tool result(s)")
    if status.summary_created:
        parts.append("summary created")
    elif status.breaker_active:
        parts.append("summary breaker active")
    elif status.error_message:
        parts.append(f"summary failed: {status.error_message}")
    if status.estimated_tokens:
        parts.append(f"estimated {status.estimated_tokens} tokens")
    if not parts and manual:
        if status.skipped_reason == "within_budget":
            return "no compression needed"
        if status.skipped_reason == "nothing_to_summarize":
            return "nothing to summarize"
        return status.skipped_reason or "no compression needed"
    return "; ".join(parts) if parts else None


def _prompt_for_mode(mode: AgentMode, tool_executor: ToolExecutor | None = None) -> str:
    permission_label = ""
    if tool_executor is not None:
        permission_label = f" | {_permission_mode_label(tool_executor.permission_manager.mode)}"
    if mode == AgentMode.PLAN:
        return f"You [plan{permission_label}] > "
    if permission_label:
        return f"You [{permission_label.removeprefix(' | ')}] > "
    return "You > "


def _cycle_permission_mode(tool_executor: ToolExecutor) -> str:
    current = PermissionMode(tool_executor.permission_manager.mode)
    index = PERMISSION_MODE_CYCLE.index(current)
    next_mode = PERMISSION_MODE_CYCLE[(index + 1) % len(PERMISSION_MODE_CYCLE)]
    tool_executor.permission_manager.mode = next_mode
    return _permission_mode_label(next_mode)


def _permission_mode_label(mode: PermissionMode | str) -> str:
    return PERMISSION_MODE_LABELS[PermissionMode(mode)]


def _confirm_plan(stdout: TextIO, stdin: TextIO) -> bool:
    _write_text(stdout, "确认执行这个计划吗？[y/n] > ")
    stdout.flush()
    answer = stdin.readline()
    if answer == "":
        _write_text(stdout, "\n")
        stdout.flush()
        return False
    return answer.strip().lower() in {"y", "yes"}


def _plan_from_latest_response(state: LoopState) -> PlanDocument | None:
    for message in reversed(state.session.messages):
        if message.role != "assistant" or not isinstance(message.content, str):
            continue
        text = message.content.strip()
        if not text:
            return None
        try:
            return parse_plan(text)
        except ValueError:
            # Preserve the existing free-form plan flow while giving it one
            # explicit step that can still be checkpointed and resumed.
            return PlanDocument(
                plan_id=f"plan_{uuid.uuid4().hex[:8]}",
                steps=(PlanStep(id="step_1", description=_compact(text, limit=500)),),
                status=PlanStatus.AWAITING_CONFIRMATION,
            )
    return None


def _plan_text(plan: PlanDocument) -> str:
    lines = [
        f"{index}. {step.description}"
        for index, step in enumerate(plan.steps, start=1)
        if step.status != PlanStatus.COMPLETED
    ]
    return "\n".join(lines)


def _format_plan(plan: PlanDocument) -> str:
    lines = [f"[plan {plan.plan_id}] status={plan.status.value}"]
    for step in plan.steps:
        marker = {
            PlanStatus.COMPLETED: "x",
            PlanStatus.RUNNING: ">",
            PlanStatus.FAILED: "!",
            PlanStatus.UNKNOWN: "?",
        }.get(step.status, " ")
        suffix = f" ({step.error})" if step.error else ""
        lines.append(f"  [{marker}] {step.id}: {step.description}{suffix}")
    return "\n".join(lines)


def _compact(text: str, *, limit: int = 160) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _risk_text(request: PermissionRequest) -> str:
    labels = {
        "run_command": "会在当前 workspace 执行 shell 命令",
        "write_file": "会创建或覆盖 workspace 内文件",
        "edit_file": "会修改 workspace 内文件",
    }
    return labels.get(request.tool_name, request.risk_summary)
