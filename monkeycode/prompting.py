from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from monkeycode.events import AgentMode
from monkeycode.messages import ToolDefinition


FIXED_MODULE_ORDER = (
    "identity",
    "system_constraints",
    "task_modes",
    "action_execution",
    "tool_usage",
    "tone_style",
    "text_output",
)

OPTIONAL_MODULE_ORDER = (
    "custom_instructions",
    "skills_catalog",
    "long_term_memory",
)


@dataclass(frozen=True)
class PromptModule:
    name: str
    priority: int
    content: str
    stable: bool = True


@dataclass(frozen=True)
class PromptContext:
    workspace_root: str
    mode: AgentMode
    turn_index: int = 0
    iteration: int = 1
    available_tools: list[ToolDefinition] = field(default_factory=list)
    cwd: str | None = None
    extra_environment: dict[str, str] = field(default_factory=dict)
    dynamic_context_blocks: list[str] = field(default_factory=list)

    @classmethod
    def from_runtime(
        cls,
        *,
        workspace_root: str | Path,
        mode: AgentMode,
        turn_index: int,
        iteration: int,
        available_tools: list[ToolDefinition] | None = None,
        cwd: str | Path | None = None,
        extra_environment: dict[str, str] | None = None,
        dynamic_context_blocks: list[str] | None = None,
    ) -> PromptContext:
        return cls(
            workspace_root=str(workspace_root),
            cwd=str(cwd) if cwd is not None else str(Path.cwd()),
            mode=mode,
            turn_index=turn_index,
            iteration=iteration,
            available_tools=list(available_tools or []),
            extra_environment=dict(extra_environment or {}),
            dynamic_context_blocks=list(dynamic_context_blocks or []),
        )


@dataclass(frozen=True)
class DynamicInstruction:
    kind: str
    content: str
    mode: AgentMode | None = None
    level: str = "full"

    def tagged(self) -> str:
        mode_attr = f' mode="{self.mode.value}"' if self.mode else ""
        return (
            f'<monkeycode_instruction type="{self.kind}"{mode_attr} level="{self.level}">\n'
            f"{self.content.strip()}\n"
            "</monkeycode_instruction>"
        )


@dataclass(frozen=True)
class PromptRenderResult:
    stable_system_text: str
    dynamic_system_messages: list[str]
    modules: list[PromptModule]


@dataclass(frozen=True)
class ProviderPromptPayload:
    stable_system_text: str
    dynamic_system_messages: list[str] = field(default_factory=list)
    stable_cacheable: bool = True


@dataclass(frozen=True)
class SkillCatalogItem:
    name: str
    description: str


@dataclass(frozen=True)
class ActiveSkillEntry:
    name: str
    body: str


class ModeInjectionState:
    def __init__(self, *, repeat_turn_interval: int = 3, repeat_iteration_interval: int = 4) -> None:
        self.repeat_turn_interval = repeat_turn_interval
        self.repeat_iteration_interval = repeat_iteration_interval
        self._last_mode: AgentMode | None = None
        self._mode_start_turn = 0

    def instruction_for(self, mode: AgentMode, *, turn_index: int, iteration: int) -> DynamicInstruction:
        if self._last_mode != mode:
            self._last_mode = mode
            self._mode_start_turn = turn_index
            return _mode_instruction(mode, "full")
        if iteration > 1 and iteration % self.repeat_iteration_interval == 0:
            return _mode_instruction(mode, "medium")
        if turn_index > self._mode_start_turn and (
            turn_index - self._mode_start_turn
        ) % self.repeat_turn_interval == 0:
            return _mode_instruction(mode, "medium")
        return _mode_instruction(mode, "compact")


class PromptBuilder:
    def __init__(self, modules: list[PromptModule] | None = None) -> None:
        self.modules = modules or default_prompt_modules()

    def build(
        self,
        context: PromptContext,
        *,
        optional_modules: list[PromptModule] | None = None,
        injection_state: ModeInjectionState | None = None,
    ) -> PromptRenderResult:
        modules = _ordered_modules([*self.modules, *(optional_modules or [])])
        stable_system_text = "\n\n".join(module.content.strip() for module in modules if module.stable)
        dynamic_messages = [
            _environment_message(context),
            (injection_state or ModeInjectionState()).instruction_for(
                context.mode,
                turn_index=context.turn_index,
                iteration=context.iteration,
            ).tagged(),
        ]
        return PromptRenderResult(
            stable_system_text=stable_system_text,
            dynamic_system_messages=dynamic_messages,
            modules=modules,
        )


def to_provider_prompt_payload(rendered: PromptRenderResult) -> ProviderPromptPayload:
    return ProviderPromptPayload(
        stable_system_text=rendered.stable_system_text,
        dynamic_system_messages=list(rendered.dynamic_system_messages),
        stable_cacheable=True,
    )


def default_prompt_modules() -> list[PromptModule]:
    return [
        PromptModule(
            "identity",
            10,
            "You are MonkeyCode, a terminal AI coding assistant implemented in Python.",
        ),
        PromptModule(
            "system_constraints",
            20,
            "\n".join(
                [
                    "Follow system, developer, and user instructions in priority order.",
                    "Work inside the current workspace unless the user explicitly asks otherwise.",
                    "Do not expose secrets, API keys, or hidden configuration values.",
                    "When a request is ambiguous, make the safest useful assumption and continue.",
                ]
            ),
        ),
        PromptModule(
            "task_modes",
            30,
            "\n".join(
                [
                    "MonkeyCode has chat, plan, and execute modes.",
                    "In plan mode, inspect and reason with read-only tools only; do not modify files or run side-effectful commands.",
                    "In execute mode, perform the requested work with the available tools.",
                ]
            ),
        ),
        PromptModule(
            "action_execution",
            40,
            "\n".join(
                [
                    "Use tools when they are needed to inspect, edit, search, or run code.",
                    "Prefer the dedicated file and search tools over shell commands for workspace inspection.",
                    "Before editing a file, read the relevant file content first.",
                    "If a tool fails, use the structured error to adjust arguments or choose another tool.",
                    "For write/edit requests, distinguish the literal file content from wording such as counts, labels, or instructions. If the user quotes text, write or replace only the quoted text unless told otherwise.",
                    "Keep code changes focused on the user's request.",
                    "Preserve existing project conventions and public behavior unless the user asks for a change.",
                    "Add comments only when they clarify non-obvious logic.",
                ]
            ),
        ),
        PromptModule(
            "tool_usage",
            50,
            "\n".join(
                [
                    "Tool calls must use the provided JSON schema exactly.",
                    "Do not invent tool names or parameters.",
                    "Choose the narrowest inspection tool and make the fewest calls needed.",
                    "Use find_files only for path discovery by glob, search_code for known text or symbols, and read_file only when a known file needs full context.",
                    "Do not repeat or overlap inspection calls when existing results already answer the question.",
                    "Prefer one well-scoped search_code call over separate find_files and read_file calls when matching lines are sufficient.",
                    "Report tool results in concise user-facing language; do not dump internal JSON unless it is useful.",
                    "For exact text replacement, use the original text exactly once; if no unique match exists, report the failure and retry with better context.",
                ]
            ),
        ),
        PromptModule(
            "tone_style",
            60,
            "Answer in Chinese by default while preserving exact technical strings such as commands, paths, APIs, model names, and error text.",
        ),
        PromptModule(
            "text_output",
            70,
            "\n".join(
                [
                    "Keep normal output concise and action-oriented.",
                    "When work is complete, summarize what changed and how it was verified.",
                ]
            ),
        ),
    ]


def enhance_tool_definition(tool: ToolDefinition) -> ToolDefinition:
    rules = [
        "Follow the tool schema exactly.",
        "If the tool fails, return the structured failure so the model can recover.",
    ]
    if tool.name in {"read_file", "find_files", "search_code"}:
        rules.append("This tool is allowed in plan mode and should be preferred for inspection.")
    if tool.name in {"write_file", "edit_file"}:
        rules.append("Before modifying a file, inspect the relevant content first.")
        rules.append("Use only the literal content requested by the user; do not append count or wording instructions.")
    if tool.name == "edit_file":
        rules.append("The old text must match exactly once; zero or multiple matches must be reported as an error.")
    if tool.name == "run_command":
        rules.append("Use non-interactive commands and include the command failure output when retrying.")
    suffix = "\n\nMonkeyCode tool rules:\n- " + "\n- ".join(rules)
    if "MonkeyCode tool rules:" in tool.description:
        return tool
    return replace(tool, description=f"{tool.description.rstrip()}{suffix}")


def enhance_tool_definitions(tools: list[ToolDefinition] | None) -> list[ToolDefinition]:
    return [enhance_tool_definition(tool) for tool in tools or []]


def render_skills_catalog(items: list[SkillCatalogItem]) -> str:
    if not items:
        return ""
    lines = [
        "## Available Skills",
        "",
        *[f"- {item.name}: {item.description}" for item in items],
        "",
        'Call the LoadSkill tool with {"name": "<skill_name>"} to activate a skill\'s full SOP and specialized tools before executing it.',
    ]
    return "\n".join(lines)


def render_active_skills_block(entries: list[ActiveSkillEntry]) -> str:
    if not entries:
        return ""
    lines = ["## Active Skills"]
    for entry in entries:
        lines.extend(["", f"### Skill: {entry.name}", "", entry.body.strip()])
    return "\n".join(lines)


def _ordered_modules(modules: list[PromptModule]) -> list[PromptModule]:
    order = {name: index for index, name in enumerate((*FIXED_MODULE_ORDER, *OPTIONAL_MODULE_ORDER))}
    return sorted(modules, key=lambda module: (module.priority, order.get(module.name, 999), module.name))


def _environment_message(context: PromptContext) -> str:
    tool_names = ", ".join(tool.name for tool in context.available_tools) or "none"
    extras = "\n".join(
        f"{key}: {value}" for key, value in sorted(context.extra_environment.items())
    )
    lines = [
        '<monkeycode_context type="runtime">',
        f"workspace_root: {context.workspace_root}",
        f"cwd: {context.cwd or context.workspace_root}",
        f"mode: {context.mode.value}",
        f"turn_index: {context.turn_index}",
        f"iteration: {context.iteration}",
        f"available_tools: {tool_names}",
    ]
    if extras:
        lines.append(extras)
    for block in context.dynamic_context_blocks:
        if block.strip():
            lines.append(block.strip())
    lines.append("</monkeycode_context>")
    return "\n".join(lines)


def _mode_instruction(mode: AgentMode, level: str) -> DynamicInstruction:
    if mode == AgentMode.PLAN:
        content_by_level = {
            "full": "Current mode is PLAN. Use only read-only inspection tools. Produce a clear plan and do not edit files, write files, or run side-effectful commands.",
            "medium": "PLAN mode is still active: inspect only, then plan.",
            "compact": "PLAN mode: read-only tools only.",
        }
    elif mode == AgentMode.EXECUTE:
        content_by_level = {
            "full": "Current mode is EXECUTE. Use tools to complete the user's task, recover from tool errors, and stop when the task is done.",
            "medium": "EXECUTE mode is active: keep using tools until the task is complete or a stop condition applies.",
            "compact": "EXECUTE mode: do the work with tools as needed.",
        }
    else:
        content_by_level = {
            "full": "Current mode is CHAT. Answer conversationally and use tools only if they are available and clearly needed.",
            "medium": "CHAT mode is active.",
            "compact": "CHAT mode.",
        }
    return DynamicInstruction("mode", content_by_level[level], mode=mode, level=level)
