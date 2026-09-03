from io import StringIO
from io import BytesIO, TextIOWrapper
import json
import sys
from types import SimpleNamespace

import monkeycode.tui as tui
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.keys import Keys
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.data_structures import Point
from prompt_toolkit.output import DummyOutput
from monkeycode.config import AppConfig, SecretValue
from monkeycode.errors import AuthenticationError
from monkeycode.messages import StreamEvent
from monkeycode.permissions import PermissionMode
from monkeycode.skills.active import ActiveSkills
from monkeycode.session_archive import SessionArchive
from monkeycode.tools import create_default_executor
from monkeycode.tui import render_startup_banner, run_chat_loop


class FakeProvider:
    def __init__(self) -> None:
        self.calls = []

    def stream_chat(self, messages):
        self.calls.append(list(messages))
        yield StreamEvent(type="text_delta", text="Hel")
        yield StreamEvent(type="text_delta", text="lo")
        yield StreamEvent(type="done")


class FailingProvider:
    def __init__(self) -> None:
        self.calls = []

    def stream_chat(self, messages):
        self.calls.append(list(messages))
        raise AuthenticationError("authentication failed")


class EmojiProvider:
    def stream_chat(self, messages):
        yield StreamEvent(type="text_delta", text="hello ")
        yield StreamEvent(type="text_delta", text="😊")
        yield StreamEvent(type="done")


class CacheProvider:
    def __init__(self) -> None:
        self.calls = []

    def stream_chat(self, messages, *, prompt_payload=None):
        self.calls.append(list(messages))
        yield StreamEvent(
            type="usage",
            usage={"total_tokens": 10},
            cache_usage={"provider": "openai", "available": True, "cached_tokens": 4},
        )
        yield StreamEvent(type="text_delta", text="ok")
        yield StreamEvent(type="done")


class ModeCaptureProvider:
    def __init__(self) -> None:
        self.calls = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append(
            {
                "messages": list(messages),
                "tool_names": [tool.name for tool in tools or []],
                "dynamic_system_messages": list(prompt_payload.dynamic_system_messages),
            }
        )
        yield StreamEvent(type="text_delta", text="ok")
        yield StreamEvent(type="done")


def config() -> AppConfig:
    return AppConfig(
        protocol="openai",
        model="gpt-test",
        base_url="https://example.test",
        api_key=SecretValue("sk-secret"),
        options={},
    )


def test_prints_streaming_reply_and_preserves_context() -> None:
    provider = FakeProvider()
    stdin = StringIO("hi\nagain\n/exit\n")
    stdout = StringIO()

    code = run_chat_loop(config(), provider, stdin=stdin, stdout=stdout)

    output = stdout.getvalue()
    assert code == 0
    assert "sk-secret" not in output
    assert "Hello" in output
    assert len(provider.calls) == 2
    assert provider.calls[1][0].content == "hi"
    assert provider.calls[1][1].content == "Hello"
    assert provider.calls[1][2].content == "again"


def test_startup_banner_contains_claude_style_panel_and_pixel_monkey() -> None:
    banner = render_startup_banner(config(), cwd="C:\\Users\\ROG", accent_code=208)

    assert "\x1b[38;5;208m" in banner
    assert "\x1b[48;5;208m" in banner
    assert "MonkeyCode" in banner
    assert "Welcome back!" in banner
    assert "gpt-test" in banner
    assert "C:\\Users\\ROG" in banner
    assert "+" in banner
    assert "|" in banner
    assert "-" in banner


def test_interactive_startup_clears_old_terminal_content(monkeypatch) -> None:
    stdout = StringIO()
    monkeypatch.setattr(tui, "_should_use_fixed_footer", lambda stdin, output: True)
    monkeypatch.setattr(tui, "_render_console_footer", lambda *args, **kwargs: None)
    monkeypatch.setattr(tui, "_teardown_console_footer", lambda *args, **kwargs: None)
    monkeypatch.setattr(tui, "_read_user_input", lambda *args, **kwargs: "quit\n")

    code = run_chat_loop(config(), FakeProvider(), stdin=StringIO(), stdout=stdout)

    output = stdout.getvalue()
    assert code == 0
    assert output.startswith(tui.CLEAR_TERMINAL_VIEW)
    assert output.index(tui.CLEAR_TERMINAL_VIEW) < output.index("MonkeyCode")


def test_exit_command_does_not_call_provider() -> None:
    provider = FakeProvider()

    code = run_chat_loop(config(), provider, stdin=StringIO("quit\n"), stdout=StringIO())

    assert code == 0
    assert provider.calls == []


def test_exit_command_ignores_leading_bom() -> None:
    provider = FakeProvider()

    code = run_chat_loop(config(), provider, stdin=StringIO("\ufeffquit\n"), stdout=StringIO())

    assert code == 0
    assert provider.calls == []


def test_exit_command_ignores_powershell_mojibake_bom() -> None:
    provider = FakeProvider()

    code = run_chat_loop(config(), provider, stdin=StringIO("\u9518\u7e2cuit\n"), stdout=StringIO())

    assert code == 0
    assert provider.calls == []


def test_mode_commands_are_printed_without_calling_provider() -> None:
    provider = FakeProvider()
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        stdin=StringIO("/plan\n/default\n/cancel\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "[mode] plan" in output
    assert "[mode] execute" in output
    assert "[agent] cancelled" in output


def test_compact_command_prints_status_without_calling_provider() -> None:
    provider = FakeProvider()
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        stdin=StringIO("/compact\nquit\n"),
        stdout=stdout,
    )

    assert code == 0
    assert provider.calls == []
    assert "[context] no compression needed" in stdout.getvalue()
    assert "\n  \x1b[90m[context] no compression needed\x1b[0m\n" in stdout.getvalue()


def test_plan_mode_confirmation_can_switch_back_to_execute_mode(tmp_path) -> None:
    provider = ModeCaptureProvider()
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path),
        stdin=StringIO("/plan\ninspect\ny\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert "[mode] plan" in output
    assert "You [plan | Default permissions] >" in output
    assert "确认执行这个计划吗？[y/n] >" in output
    assert "[mode] execute" in output
    assert len(provider.calls) == 2
    assert provider.calls[0]["tool_names"] == ["read_file", "find_files", "search_code", "load_skill"]
    assert "write_file" in provider.calls[1]["tool_names"]
    assert "run_command" in provider.calls[1]["tool_names"]
    assert any("mode: plan" in message for message in provider.calls[0]["dynamic_system_messages"])
    assert any("mode: execute" in message for message in provider.calls[1]["dynamic_system_messages"])
    assert provider.calls[1]["messages"][-1].content == "用户已确认计划，请按刚才的计划执行。"


def test_plan_mode_confirmation_no_keeps_plan_mode(tmp_path) -> None:
    provider = ModeCaptureProvider()
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        tool_executor=create_default_executor(tmp_path),
        stdin=StringIO("/plan\ninspect\nn\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert len(provider.calls) == 1
    assert "[plan] 未确认，保持计划模式" in output
    assert "[mode] execute" not in output


def test_shift_tab_cycles_permission_modes(tmp_path) -> None:
    provider = FakeProvider()
    executor = create_default_executor(tmp_path)
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        tool_executor=executor,
        stdin=StringIO("\x1b[Z\n\x1b[Z\n\x1b[Z\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "[permissions] Auto-review" in output
    assert "[permissions] Full access" in output
    assert "[permissions] Default permissions" in output
    assert "You [Default permissions] >" in output
    assert executor.permission_manager.mode == PermissionMode.DEFAULT


def test_tab_fallback_cycles_permission_modes(tmp_path) -> None:
    provider = FakeProvider()
    executor = create_default_executor(tmp_path)
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        tool_executor=executor,
        stdin=StringIO("\t\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "[permissions] Auto-review" in output
    assert executor.permission_manager.mode == PermissionMode.STRICT


def test_windows_console_shift_tab_cycles_permission_without_enter(monkeypatch, tmp_path) -> None:
    provider = FakeProvider()
    executor = create_default_executor(tmp_path)
    stdout = StringIO()
    keys = iter(["\x00", "\x0f", "q", "u", "i", "t", "\r"])
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(keys)))
    monkeypatch.setattr(tui, "_should_use_console_reader", lambda stdin: True)

    code = tui.run_chat_loop(
        config(),
        provider,
        tool_executor=executor,
        stdin=StringIO(),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "[permissions]" not in output
    assert "\r\033[2KYou [Auto-review] > quit" in output
    assert executor.permission_manager.mode == PermissionMode.STRICT


def test_windows_console_vt_shift_tab_cycles_permission(monkeypatch, tmp_path) -> None:
    provider = FakeProvider()
    executor = create_default_executor(tmp_path)
    stdout = StringIO()
    keys = ["\x1b", "[", "Z", "q", "u", "i", "t", "\r"]
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(getwch=lambda: keys.pop(0), kbhit=lambda: bool(keys)),
    )
    monkeypatch.setattr(tui, "_should_use_console_reader", lambda stdin: True)

    code = tui.run_chat_loop(
        config(),
        provider,
        tool_executor=executor,
        stdin=StringIO(),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "[permissions]" not in output
    assert "\r\033[2KYou [Auto-review] > quit" in output
    assert executor.permission_manager.mode == PermissionMode.STRICT


def test_provider_error_does_not_add_failed_assistant_history() -> None:
    provider = FailingProvider()
    stdin = StringIO("hi\n/exit\n")
    stdout = StringIO()

    code = run_chat_loop(config(), provider, stdin=stdin, stdout=stdout)

    assert code == 0
    assert "authentication failed" in stdout.getvalue()
    assert len(provider.calls) == 1
    assert [message.role for message in provider.calls[0]] == ["user"]


def test_non_gbk_model_output_does_not_crash_windows_stdout() -> None:
    stdout = TextIOWrapper(BytesIO(), encoding="gbk", errors="strict")

    code = run_chat_loop(
        config(),
        EmojiProvider(),
        stdin=StringIO("hi\nquit\n"),
        stdout=stdout,
    )

    assert code == 0


def test_hides_cache_summary_when_provider_reports_cache_usage() -> None:
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        CacheProvider(),
        stdin=StringIO("hi\nquit\n"),
        stdout=stdout,
    )

    assert code == 0
    assert "[cache]" not in stdout.getvalue()
    assert "openai cached=4" not in stdout.getvalue()


def test_local_state_writes_session_archive_without_startup_status(tmp_path) -> None:
    (tmp_path / "MONKEYCODE.md").write_text("project instructions", encoding="utf-8")
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        FakeProvider(),
        workspace_root=tmp_path,
        enable_local_state=True,
        stdin=StringIO("hi\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert "[session]" not in output
    assert "已就绪" not in output
    sessions = list((tmp_path / ".monkeycode" / "sessions").glob("*.jsonl"))
    assert len(sessions) == 1
    event_types = [json.loads(line)["type"] for line in sessions[0].read_text(encoding="utf-8").splitlines()]
    assert "session_started" in event_types
    assert "user_message" in event_types
    assert "assistant_message" in event_types
    assert "session_ended" in event_types


def test_resume_missing_session_starts_with_diagnostic(tmp_path) -> None:
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        FakeProvider(),
        workspace_root=tmp_path,
        enable_local_state=True,
        resume_session_id="missing-session",
        stdin=StringIO("quit\n"),
        stdout=stdout,
    )

    assert code == 0
    output = stdout.getvalue()
    assert "restore_diagnostics=1" not in output
    assert "[session]" not in output


def test_help_lists_builtins_without_calling_provider() -> None:
    provider = FakeProvider()
    stdout = StringIO()

    code = run_chat_loop(config(), provider, stdin=StringIO("/help\nquit\n"), stdout=stdout)

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    for name in [
        "/clear",
        "/compact",
        "/commit",
        "/do",
        "/exit",
        "/help",
        "/memory",
        "/permission",
        "/plan",
        "/resume",
        "/review",
        "/session",
        "/skill",
        "/status",
        "/test",
    ]:
        assert name in output
    assert "/default" not in output


def test_unknown_slash_command_does_not_call_provider() -> None:
    provider = FakeProvider()
    stdout = StringIO()

    code = run_chat_loop(config(), provider, stdin=StringIO("/foobar\nquit\n"), stdout=stdout)

    assert code == 0
    assert provider.calls == []
    assert "未知命令" in stdout.getvalue()


def test_slash_dispatch_is_case_insensitive() -> None:
    provider = FakeProvider()
    stdout = StringIO()

    code = run_chat_loop(config(), provider, stdin=StringIO("/Help\nquit\n"), stdout=stdout)

    assert code == 0
    assert provider.calls == []
    assert "/status" in stdout.getvalue()


def test_status_memory_permission_and_session_commands(tmp_path) -> None:
    provider = CacheProvider()
    memory_root = tmp_path / ".monkeycode" / "memory"
    (memory_root / "preference").mkdir(parents=True)
    (memory_root / "preference" / "note.md").write_text("note", encoding="utf-8")
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        workspace_root=tmp_path,
        enable_local_state=True,
        tool_executor=create_default_executor(tmp_path),
        stdin=StringIO("hi\n/status\n/memory\n/permission\n/session\nquit\n"),
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert code == 0
    assert len(provider.calls) >= 1
    assert "MonkeyCode Status" in output
    assert "10 total" in output
    assert "note.md" in output
    assert "execute" in output
    assert "Session:" in output
    assert "Path:" in output


def test_review_injects_prompt_and_calls_provider() -> None:
    provider = FakeProvider()

    code = run_chat_loop(config(), provider, stdin=StringIO("/review\nquit\n"), stdout=StringIO())

    assert code == 0
    assert len(provider.calls) == 1
    assert "# Review Skill" in provider.calls[0][0].content
    assert "This skill is designed to use only these tools:" in provider.calls[0][0].content


def test_skill_command_lists_builtin_skills(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    provider = FakeProvider()
    stdout = StringIO()

    code = run_chat_loop(config(), provider, stdin=StringIO("/skill\nquit\n"), stdout=stdout)

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "Available skills (3):" in output
    assert "/commit" in output
    assert "/review" in output
    assert "/test" in output


def test_commit_skill_injects_prompt_and_calls_provider() -> None:
    provider = FakeProvider()

    code = run_chat_loop(config(), provider, stdin=StringIO("/commit\nquit\n"), stdout=StringIO())

    assert code == 0
    assert len(provider.calls) == 1
    assert "# Commit Skill" in provider.calls[0][0].content
    assert "This skill is designed to use only these tools:" in provider.calls[0][0].content


def test_clear_command_clears_active_skills() -> None:
    active = ActiveSkills()
    active.activate("commit", "Body")
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        FakeProvider(),
        active_skills=active,
        stdin=StringIO("/clear\nquit\n"),
        stdout=stdout,
    )

    assert code == 0
    assert active.names() == []
    output = stdout.getvalue()
    assert tui.CLEAR_TERMINAL_VIEW in output
    after_clear = output.split(tui.CLEAR_TERMINAL_VIEW, 1)[1]
    assert "MonkeyCode" in after_clear
    assert "Welcome back!" in after_clear
    assert "已清空当前会话" not in output


def test_clear_starts_new_session_archive(tmp_path) -> None:
    provider = FakeProvider()
    stdout = StringIO()

    code = run_chat_loop(
        config(),
        provider,
        workspace_root=tmp_path,
        enable_local_state=True,
        stdin=StringIO("hi\n/clear\n/session\nquit\n"),
        stdout=stdout,
    )

    assert code == 0
    output = stdout.getvalue()
    assert tui.CLEAR_TERMINAL_VIEW in output
    after_clear = output.split(tui.CLEAR_TERMINAL_VIEW, 1)[1]
    assert "MonkeyCode" in after_clear
    assert "Welcome back!" in after_clear
    assert "已清空当前会话" not in output
    sessions = list((tmp_path / ".monkeycode" / "sessions").glob("*.jsonl"))
    assert len(sessions) == 2


def test_resume_restores_selected_session(tmp_path) -> None:
    archive = SessionArchive.create(tmp_path)
    archive.append_user_message("old question")
    archive.append_assistant_message("old answer")
    provider = FakeProvider()

    code = run_chat_loop(
        config(),
        provider,
        workspace_root=tmp_path,
        enable_local_state=True,
        stdin=StringIO("/resume\n1\nnew question\nquit\n"),
        stdout=StringIO(),
    )

    assert code == 0
    assert len(provider.calls) >= 1
    assert [message.content for message in provider.calls[0][:3]] == [
        "old question",
        "old answer",
        "new question",
    ]


def test_windows_console_completion_accepts_tab(monkeypatch) -> None:
    provider = FakeProvider()
    stdout = StringIO()
    keys = iter(["/", "s", "\t", "q", "u", "i", "t", "\r"])
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(keys)))
    monkeypatch.setattr(tui, "_should_use_console_reader", lambda stdin: True)

    code = tui.run_chat_loop(config(), provider, stdin=StringIO(), stdout=stdout)

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "Session:" in output
    assert "\n> /session" not in output


def test_windows_console_completion_renders_inline_without_new_rows(monkeypatch) -> None:
    provider = FakeProvider()
    stdout = StringIO()
    keys = iter(["/", "p", "l", "a", "n", "\r", "q", "u", "i", "t", "\r"])
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(keys)))
    monkeypatch.setattr(tui, "_should_use_console_reader", lambda stdin: True)

    code = tui.run_chat_loop(config(), provider, stdin=StringIO(), stdout=stdout)

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "\n> /plan" not in output
    assert "\r\033[2KYou > /pl" in output
    assert "[mode] plan" in output


def test_windows_console_backspace_erases_wide_character(monkeypatch) -> None:
    provider = FakeProvider()
    stdout = StringIO()
    keys = iter(["\u7684", "\b", "q", "u", "i", "t", "\r"])
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(keys)))
    monkeypatch.setattr(tui, "_should_use_console_reader", lambda stdin: True)

    code = tui.run_chat_loop(config(), provider, stdin=StringIO(), stdout=stdout)

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "\u7684\b \b\b \bq" in output


def test_windows_console_backspace_clears_inline_completion_hint(monkeypatch) -> None:
    provider = FakeProvider()
    stdout = StringIO()
    keys = iter(["/", "\b", "q", "u", "i", "t", "\r"])
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(keys)))
    monkeypatch.setattr(tui, "_should_use_console_reader", lambda stdin: True)

    code = tui.run_chat_loop(config(), provider, stdin=StringIO(), stdout=stdout)

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert "\r\033[2KYou > /\033[2mclear\033[0m" in output
    assert "\b \b\r\033[2KYou > q" in output


def test_windows_console_paste_slash_command_does_not_render_per_character(monkeypatch) -> None:
    provider = FakeProvider()
    stdout = StringIO()
    keys = list("/permission\rquit\r")

    class FakeMsvcrt:
        paste_chars_remaining = len("/permission")

        def getwch(self):
            char = keys.pop(0)
            if self.paste_chars_remaining:
                self.paste_chars_remaining -= 1
            return char

        def kbhit(self):
            return self.paste_chars_remaining > 0

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt())
    monkeypatch.setattr(tui, "_should_use_console_reader", lambda stdin: True)

    code = tui.run_chat_loop(config(), provider, stdin=StringIO(), stdout=stdout)

    output = stdout.getvalue()
    assert code == 0
    assert provider.calls == []
    assert output.count("You [Default permissions] >") <= 3
    assert "\n> /permission" not in output
    assert "execute" in output


def test_windows_console_multiline_paste_is_returned_as_one_input(monkeypatch) -> None:
    stdout = StringIO()
    pasted = list("first\r\nsecond")
    keys = pasted + ["\r"]

    class FakeMsvcrt:
        paste_chars_remaining = len(pasted)

        def getwch(self):
            char = keys.pop(0)
            if self.paste_chars_remaining:
                self.paste_chars_remaining -= 1
            return char

        def kbhit(self):
            return self.paste_chars_remaining > 0

    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt())

    result = tui._read_console_line(
        stdout,
        tui.AgentMode.EXECUTE,
        None,
        tui.Registry(),
    )

    assert result == "first\nsecond\n"


def test_fixed_console_footer_renders_input_and_status_rows(monkeypatch, tmp_path) -> None:
    executor = create_default_executor(tmp_path)
    stdout = StringIO()
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (80, 24))

    tui._render_console_footer(
        stdout,
        tui.AgentMode.EXECUTE,
        executor,
        "deepseek-v4-pro",
    )

    output = stdout.getvalue()
    assert "\033[1;20r" in output
    assert "\033[21;1H\033[2K" + tui.DIM + "-" * 80 in output
    assert "\033[22;1H\033[2K" in output
    assert "Send a message..." in output
    assert "\033[23;1H\033[2K" + tui.DIM + "-" * 80 in output
    assert "\033[24;1H\033[2K" in output
    assert "Default permissions" in output
    assert "shift+tab to cycle" in output
    assert "deepseek-v4-pro" not in output


def test_fixed_console_footer_expands_to_show_multiline_input(monkeypatch, tmp_path) -> None:
    executor = create_default_executor(tmp_path)
    stdout = StringIO()
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (80, 24))

    tui._render_console_footer(
        stdout,
        tui.AgentMode.EXECUTE,
        executor,
        "deepseek-v4-pro",
        list("first\nsecond"),
    )

    output = stdout.getvalue()
    assert "\033[20;1H\033[2K" + tui.DIM + "-" * 80 in output
    assert "\033[21;1H\033[2K" in output
    assert "first" in output
    assert "\033[22;1H\033[2K" in output
    assert "second" in output
    assert "\033[23;1H\033[2K" + tui.DIM + "-" * 80 in output
    assert "\033[24;1H\033[2K" in output


def test_fixed_console_footer_commits_input_to_scroll_region(monkeypatch, tmp_path) -> None:
    executor = create_default_executor(tmp_path)
    stdout = StringIO()
    keys = iter(["h", "i", "\r"])
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(keys)))
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (80, 24))

    result = tui._read_console_line(
        stdout,
        tui.AgentMode.EXECUTE,
        executor,
        tui.Registry(),
        model_name="deepseek-v4-pro",
        fixed_footer=True,
    )

    output = stdout.getvalue()
    assert result == "hi\n"
    assert "\033[21;1H\033[2K" in output
    assert "\033[20;1H\033[2KYou > hi\n" in output


def test_fixed_console_footer_uses_ime_compatible_line_input(monkeypatch) -> None:
    stdout = StringIO()
    stdin = StringIO("中文输入\n")
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (80, 24))

    result = tui._read_user_input(
        stdin,
        stdout,
        tui.AgentMode.EXECUTE,
        None,
        tui.Registry(),
        model_name="deepseek-v4-pro",
        fixed_footer=True,
    )

    assert result == "中文输入\n"
    assert "\033[20;1H\033[2KYou > 中文输入\n" in stdout.getvalue()


def test_fixed_console_footer_permission_toggle_redraws_status(monkeypatch, tmp_path) -> None:
    executor = create_default_executor(tmp_path)
    stdout = StringIO()
    keys = iter(["\x00", "\x0f", "q", "u", "i", "t", "\r"])
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(keys)))
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (80, 24))

    tui._read_console_line(
        stdout,
        tui.AgentMode.EXECUTE,
        executor,
        tui.Registry(),
        model_name="deepseek-v4-pro",
        fixed_footer=True,
    )

    output = stdout.getvalue()
    assert "Auto-review" in output
    assert "[permissions]" not in output
    assert executor.permission_manager.mode == PermissionMode.STRICT


def test_fixed_console_footer_adapts_to_narrow_terminal(monkeypatch, tmp_path) -> None:
    executor = create_default_executor(tmp_path)
    stdout = StringIO()
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (32, 10))

    tui._render_console_footer(
        stdout,
        tui.AgentMode.EXECUTE,
        executor,
        "deepseek-v4-pro-with-long-name",
    )

    status = stdout.getvalue().split("\033[10;1H\033[2K", 1)[1].split(tui.RESET, 1)[0]
    assert tui._visible_len(status) <= 32


def test_fixed_console_footer_clears_old_rows_after_terminal_resize(
    monkeypatch,
    tmp_path,
) -> None:
    executor = create_default_executor(tmp_path)
    stdout = StringIO()
    footer_state = tui.ConsoleFooterState()
    terminal_size = [80, 24]
    monkeypatch.setattr(
        tui.shutil,
        "get_terminal_size",
        lambda fallback: tuple(terminal_size),
    )

    tui._render_console_footer(
        stdout,
        tui.AgentMode.EXECUTE,
        executor,
        "deepseek-v4-pro",
        footer_state=footer_state,
    )
    stdout.seek(0)
    stdout.truncate(0)
    terminal_size[:] = [120, 40]

    tui._render_console_footer(
        stdout,
        tui.AgentMode.EXECUTE,
        executor,
        "deepseek-v4-pro",
        footer_state=footer_state,
    )

    output = stdout.getvalue()
    assert "\033[21;1H\033[2K" in output
    assert "\033[37;1H\033[2K" + tui.DIM + "-" * 120 in output
    assert "\033[39;1H\033[2K" + tui.DIM + "-" * 120 in output
    assert "\033[40;1H\033[2K" in output

    stdout.seek(0)
    stdout.truncate(0)
    terminal_size[:] = [60, 16]
    tui._render_console_footer(
        stdout,
        tui.AgentMode.EXECUTE,
        executor,
        "deepseek-v4-pro",
        footer_state=footer_state,
    )

    output = stdout.getvalue()
    assert "\033[13;1H\033[2K" + tui.DIM + "-" * 60 in output
    assert "\033[15;1H\033[2K" + tui.DIM + "-" * 60 in output
    assert "\033[16;1H\033[2K" in output


def test_console_resize_event_redraws_footer(monkeypatch, tmp_path) -> None:
    executor = create_default_executor(tmp_path)
    stdout = StringIO()
    footer_state = tui.ConsoleFooterState(
        terminal_columns=80,
        terminal_lines=24,
    )
    keys = iter([None, "q", "u", "i", "t", "\r"])
    source = SimpleNamespace(getwch=lambda: next(keys), kbhit=lambda: False)
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (120, 40))

    result = tui._read_console_line_from_source(
        source,
        stdout,
        tui.AgentMode.EXECUTE,
        executor,
        tui.Registry(),
        model_name="deepseek-v4-pro",
        fixed_footer=True,
        footer_state=footer_state,
    )

    output = stdout.getvalue()
    assert result == "quit\n"
    assert "\033[21;1H\033[2K" in output
    assert "\033[37;1H\033[2K" + tui.DIM + "-" * 120 in output
    assert footer_state.terminal_columns == 120
    assert footer_state.terminal_lines == 40


def test_fixed_footer_hides_placeholder_on_first_character(monkeypatch) -> None:
    stdout = StringIO()
    keys = iter(["x", "\b", "q", "u", "i", "t", "\r"])
    source = SimpleNamespace(getwch=lambda: next(keys), kbhit=lambda: False)
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (80, 24))

    result = tui._read_console_line_from_source(
        source,
        stdout,
        tui.AgentMode.EXECUTE,
        None,
        tui.Registry(),
        model_name="deepseek-v4-pro",
        fixed_footer=True,
    )

    output = stdout.getvalue()
    assert result == "quit\n"
    assert f"\033[22;1H\033[2K{tui.BLUE}›{tui.RESET} x" in output
    assert "xend a message..." not in output
    assert tui.INPUT_PLACEHOLDER in output


def test_fixed_footer_slash_command_shows_completion_hint(monkeypatch) -> None:
    stdout = StringIO()
    keys = iter(["/", "\b", "q", "u", "i", "t", "\r"])
    source = SimpleNamespace(getwch=lambda: next(keys), kbhit=lambda: False)
    registry = tui.Registry()
    tui.register_builtins(registry)
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (80, 24))

    result = tui._read_console_line_from_source(
        source,
        stdout,
        tui.AgentMode.EXECUTE,
        None,
        registry,
        model_name="deepseek-v4-pro",
        fixed_footer=True,
    )

    output = stdout.getvalue()
    assert result == "quit\n"
    assert f"/{tui.DIM}clear{tui.RESET}" in output


def test_prompt_toolkit_slash_completer_lists_commands() -> None:
    registry = tui.Registry()
    tui.register_builtins(registry)
    completer = tui._SlashCommandCompleter(registry)

    completions = list(
        completer.get_completions(
            Document("/st"),
            CompleteEvent(completion_requested=True),
        )
    )

    assert [completion.text for completion in completions] == ["status"]
    assert completions[0].start_position == -2


def test_prompt_toolkit_slash_suggests_first_matching_command() -> None:
    registry = tui.Registry()
    tui.register_builtins(registry)
    suggest = tui._SlashCommandSuggest(registry)

    suggestion = suggest.get_suggestion(Buffer(), Document("/"))

    assert suggestion is not None
    assert suggestion.text == "clear"


def test_prompt_toolkit_input_owns_placeholder_and_footer(monkeypatch) -> None:
    captured = {}

    class FakePromptSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.layout = SimpleNamespace(
                container=tui.HSplit([]),
                current_window=SimpleNamespace(height=None),
            )
            self.app = SimpleNamespace(full_screen=False)
            captured["container"] = self.layout.container
            captured["input_window"] = self.layout.current_window
            captured["session"] = self

        def prompt(self, message):
            captured["message"] = message()
            captured["toolbar"] = captured["bottom_toolbar"]()
            return "hello"

    stdout = StringIO()
    monkeypatch.setattr(tui, "PromptSession", FakePromptSession)
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (80, 24))

    result = tui._read_prompt_toolkit_line(
        stdout,
        tui.AgentMode.EXECUTE,
        None,
        tui.Registry(),
        "deepseek-v4-pro",
        banner_text="banner one\nbanner two\n",
        transcript="You > hello\n\nworld",
    )

    assert result == "hello\n"
    assert captured["placeholder"] == [("class:placeholder", "Send a message...")]
    assert captured["erase_when_done"] is True
    assert captured["reserve_space_for_menu"] == 0
    assert captured["message"][0] == ("class:separator", "-" * 79)
    assert captured["toolbar"][0] == ("class:separator", "-" * 79)
    assert captured["container"].align == tui.VerticalAlign.BOTTOM
    input_height = captured["input_window"].height
    assert (input_height.min, input_height.max, input_height.preferred) == (1, 1, 1)
    status = captured["toolbar"][-1][1]
    assert status == "Tools disabled (shift+tab to cycle)"
    assert "deepseek-v4-pro" not in status
    assert captured["session"].app.full_screen is True
    root = captured["session"].layout.container
    assert isinstance(root, tui.HSplit)
    assert len(root.children) == 3
    assert root.children[0].height.preferred == 2
    toolbar_style = captured["style"].get_attrs_for_style_str(
        "class:bottom-toolbar"
    )
    assert toolbar_style.reverse is False
    assert "You > hello" in stdout.getvalue()


def test_prompt_transcript_keeps_user_and_assistant_messages() -> None:
    session = tui.ChatSession()
    session.add_user_message("question")
    session.add_assistant_message("answer")
    session.add_tool_result("tool-1", "hidden tool payload")

    transcript = tui._render_session_transcript(session)

    assert transcript == "You > question\n\nanswer"


def test_persistent_tui_has_one_full_screen_three_part_layout(monkeypatch) -> None:
    monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (100, 30))
    with create_pipe_input() as pipe_input:
        screen = tui.PersistentTui(
            config(),
            None,
            tui.Registry(),
            mode_getter=lambda: tui.AgentMode.EXECUTE,
            initial_transcript="restored history",
            prompt_input=pipe_input,
            prompt_output=DummyOutput(),
        )

        root = screen._session.layout.container
        assert isinstance(root, tui.HSplit)
        assert len(root.children) == 3
        assert screen._session.app.full_screen is True
        assert screen._session.app.renderer.full_screen is True
        assert screen._transcript_buffer.text == "restored history"
        assert "██" in screen._banner
        prompt_message = screen._session.message()
        assert prompt_message[0] == ("class:separator", "-" * 99)
        assert prompt_message[-1] == ("class:prompt", "› ")
        header = root.children[0].get_container()
        assert header.height.preferred == 13

        monkeypatch.setattr(tui.shutil, "get_terminal_size", lambda fallback: (60, 12))
        compact_header = root.children[0].get_container()
        assert compact_header.height.preferred == 1


def test_persistent_tui_routes_input_and_output_through_transcript() -> None:
    with create_pipe_input() as pipe_input:
        screen = tui.PersistentTui(
            config(),
            None,
            tui.Registry(),
            mode_getter=lambda: tui.AgentMode.EXECUTE,
            prompt_input=pipe_input,
            prompt_output=DummyOutput(),
        )

        screen._accept_input(SimpleNamespace(text="hello"))
        screen.write(f"{tui.STATUS_GRAY}[tool] done{tui.RESET}\n")
        screen._drain_output()

        assert screen.readline() == "hello\n"
        assert "You > hello" in screen._transcript_buffer.text
        assert "[tool] done" in screen._transcript_buffer.text
        assert "\033" not in screen._transcript_buffer.text


def test_persistent_tui_routes_input_mouse_wheel_to_transcript(monkeypatch) -> None:
    with create_pipe_input() as pipe_input:
        screen = tui.PersistentTui(
            config(),
            None,
            tui.Registry(),
            mode_getter=lambda: tui.AgentMode.EXECUTE,
            prompt_input=pipe_input,
            prompt_output=DummyOutput(),
        )
        calls = []
        monkeypatch.setattr(screen._session.layout.container.children[1], "_scroll_up", lambda: calls.append("up"))
        monkeypatch.setattr(screen._session.layout.container.children[1], "_scroll_down", lambda: calls.append("down"))
        input_window = screen._session.layout.current_window

        input_window._mouse_handler(
            MouseEvent(Point(0, 0), MouseEventType.SCROLL_UP, MouseButton.NONE, frozenset())
        )
        input_window._mouse_handler(
            MouseEvent(Point(0, 0), MouseEventType.SCROLL_DOWN, MouseButton.NONE, frozenset())
        )

        assert calls == ["up", "down"]


def test_persistent_tui_routes_generic_wheel_events_to_transcript(monkeypatch) -> None:
    with create_pipe_input() as pipe_input:
        screen = tui.PersistentTui(
            config(),
            None,
            tui.Registry(),
            mode_getter=lambda: tui.AgentMode.EXECUTE,
            prompt_input=pipe_input,
            prompt_output=DummyOutput(),
        )
        transcript = screen._session.layout.container.children[1]
        calls = []
        monkeypatch.setattr(transcript, "_scroll_up", lambda: calls.append("up"))
        monkeypatch.setattr(transcript, "_scroll_down", lambda: calls.append("down"))
        bindings = screen._session.app.key_bindings.get_bindings_for_keys((Keys.ScrollUp,))
        next(binding for binding in bindings if binding.eager()).handler(None)
        bindings = screen._session.app.key_bindings.get_bindings_for_keys((Keys.ScrollDown,))
        next(binding for binding in bindings if binding.eager()).handler(None)

        assert calls == ["up", "down"]


def test_persistent_tui_routes_up_down_keys_to_transcript(monkeypatch) -> None:
    with create_pipe_input() as pipe_input:
        screen = tui.PersistentTui(
            config(),
            None,
            tui.Registry(),
            mode_getter=lambda: tui.AgentMode.EXECUTE,
            prompt_input=pipe_input,
            prompt_output=DummyOutput(),
        )
        transcript = screen._session.layout.container.children[1]
        calls = []
        monkeypatch.setattr(transcript, "_scroll_up", lambda: calls.append("up"))
        monkeypatch.setattr(transcript, "_scroll_down", lambda: calls.append("down"))
        bindings = screen._session.app.key_bindings.get_bindings_for_keys((Keys.Up,))
        next(binding for binding in bindings if binding.eager()).handler(None)
        bindings = screen._session.app.key_bindings.get_bindings_for_keys((Keys.Down,))
        next(binding for binding in bindings if binding.eager()).handler(None)

        assert calls == ["up", "down"]


def test_persistent_tui_routes_vt100_mouse_wheel_to_transcript(monkeypatch) -> None:
    with create_pipe_input() as pipe_input:
        screen = tui.PersistentTui(
            config(),
            None,
            tui.Registry(),
            mode_getter=lambda: tui.AgentMode.EXECUTE,
            prompt_input=pipe_input,
            prompt_output=DummyOutput(),
        )
        transcript = screen._session.layout.container.children[1]
        calls = []
        monkeypatch.setattr(transcript, "_scroll_up", lambda: calls.append("up"))
        monkeypatch.setattr(transcript, "_scroll_down", lambda: calls.append("down"))
        bindings = screen._session.app.key_bindings.get_bindings_for_keys((Keys.Vt100MouseEvent,))
        handler = next(binding.handler for binding in bindings if binding.eager())
        handler(SimpleNamespace(data="\x1b[<64;10;10M"))
        handler(SimpleNamespace(data="\x1b[<65;10;10M"))

        assert calls == ["up", "down"]


def test_vt_mouse_wheel_event_type_detects_only_wheel_packets() -> None:
    assert tui._vt_mouse_wheel_event_type("\x1b[<64;10;10M") == MouseEventType.SCROLL_UP
    assert tui._vt_mouse_wheel_event_type("\x1b[<65;10;10M") == MouseEventType.SCROLL_DOWN
    assert tui._vt_mouse_wheel_event_type("\x1b[<0;10;10M") is None
