from __future__ import annotations

from typing import Protocol

from monkeycode.events import AgentMode
from monkeycode.command.skills import SkillSummary
from monkeycode.messages import ChatMessage


class UI(Protocol):
    def println(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
    def mode(self) -> AgentMode: ...
    def set_mode(self, mode: AgentMode) -> None: ...
    def inject_and_send(self, label: str, preset: str) -> None: ...
    def usage_in(self) -> int: ...
    def usage_out(self) -> int: ...
    def usage_total(self) -> int: ...
    def model_name(self) -> str: ...
    def cwd(self) -> str: ...
    def tool_count(self) -> int: ...
    def memory_files(self) -> list[str]: ...
    def session_path(self) -> str: ...
    def session_id(self) -> str: ...
    def quit(self) -> None: ...
    def force_compact(self) -> None: ...
    def open_resume_menu(self) -> None: ...
    def clear_and_new_session(self) -> None: ...
    def cancel(self) -> None: ...
    def idle(self) -> bool: ...
    def list_catalog_skills(self) -> list[SkillSummary]: ...
    def list_active_skills(self) -> list[str]: ...
    def clear_active_skills(self) -> None: ...
    def append_assistant_message(self, text: str) -> None: ...
    def recent_messages(self, n: int) -> list[ChatMessage]: ...
    def all_messages(self) -> list[ChatMessage]: ...
    def run_fork_skill(
        self,
        name: str,
        prompt: str,
        allowed_tools: list[str],
        fork_context: str,
        model: str | None,
    ) -> str: ...
    def has_catalog_skill(self, name: str) -> bool: ...
    async def execute_catalog_skill(self, name: str) -> None: ...


class NopUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.errors: list[str] = []
        self.injected: list[tuple[str, str]] = []
        self.mode_value = AgentMode.EXECUTE
        self.quit_called = False
        self.compact_called = False
        self.resume_called = False
        self.clear_called = False
        self.cancel_called = False
        self.idle_value = True
        self.catalog_skills: list[SkillSummary] = []
        self.active_skill_names: list[str] = []
        self.appended_assistant_messages: list[str] = []
        self.fork_result = ""

    def println(self, msg: str) -> None:
        self.messages.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def mode(self) -> AgentMode:
        return self.mode_value

    def set_mode(self, mode: AgentMode) -> None:
        self.mode_value = mode

    def inject_and_send(self, label: str, preset: str) -> None:
        self.injected.append((label, preset))

    def usage_in(self) -> int:
        return 0

    def usage_out(self) -> int:
        return 0

    def usage_total(self) -> int:
        return 0

    def model_name(self) -> str:
        return ""

    def cwd(self) -> str:
        return ""

    def tool_count(self) -> int:
        return 0

    def memory_files(self) -> list[str]:
        return []

    def session_path(self) -> str:
        return ""

    def session_id(self) -> str:
        return ""

    def quit(self) -> None:
        self.quit_called = True

    def force_compact(self) -> None:
        self.compact_called = True

    def open_resume_menu(self) -> None:
        self.resume_called = True

    def clear_and_new_session(self) -> None:
        self.clear_called = True

    def cancel(self) -> None:
        self.cancel_called = True

    def idle(self) -> bool:
        return self.idle_value

    def list_catalog_skills(self) -> list[SkillSummary]:
        return list(self.catalog_skills)

    def list_active_skills(self) -> list[str]:
        return list(self.active_skill_names)

    def clear_active_skills(self) -> None:
        self.active_skill_names = []

    def append_assistant_message(self, text: str) -> None:
        self.appended_assistant_messages.append(text)

    def recent_messages(self, n: int) -> list[ChatMessage]:
        return []

    def all_messages(self) -> list[ChatMessage]:
        return []

    def run_fork_skill(
        self,
        name: str,
        prompt: str,
        allowed_tools: list[str],
        fork_context: str,
        model: str | None,
    ) -> str:
        return self.fork_result

    def has_catalog_skill(self, name: str) -> bool:
        return any(skill.name == name for skill in self.catalog_skills)

    async def execute_catalog_skill(self, name: str) -> None:
        return None
