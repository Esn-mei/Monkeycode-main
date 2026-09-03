from monkeycode.events import AgentMode
from monkeycode.messages import ToolDefinition
from monkeycode.prompting import (
    ActiveSkillEntry,
    FIXED_MODULE_ORDER,
    ModeInjectionState,
    PromptBuilder,
    PromptContext,
    SkillCatalogItem,
    enhance_tool_definition,
    render_active_skills_block,
    render_skills_catalog,
    to_provider_prompt_payload,
)


def test_prompt_builder_orders_stable_modules_and_injects_runtime_context() -> None:
    tools = [ToolDefinition("read_file", "Read.", {"type": "object"})]
    rendered = PromptBuilder().build(
        PromptContext.from_runtime(
            workspace_root="C:\\repo",
            cwd="C:\\repo",
            mode=AgentMode.PLAN,
            turn_index=1,
            iteration=1,
            available_tools=tools,
        )
    )

    assert rendered.stable_system_text.index("You are MonkeyCode") < rendered.stable_system_text.index(
        "Follow system"
    )
    assert FIXED_MODULE_ORDER == (
        "identity",
        "system_constraints",
        "task_modes",
        "action_execution",
        "tool_usage",
        "tone_style",
        "text_output",
    )
    assert '<monkeycode_context type="runtime">' in rendered.dynamic_system_messages[0]
    assert "workspace_root: C:\\repo" in rendered.dynamic_system_messages[0]
    assert "available_tools: read_file" in rendered.dynamic_system_messages[0]
    assert 'mode="plan" level="full"' in rendered.dynamic_system_messages[1]


def test_mode_injection_repeats_full_then_compact_then_medium() -> None:
    state = ModeInjectionState(repeat_turn_interval=3, repeat_iteration_interval=4)

    first = state.instruction_for(AgentMode.EXECUTE, turn_index=1, iteration=1)
    second = state.instruction_for(AgentMode.EXECUTE, turn_index=2, iteration=1)
    repeated = state.instruction_for(AgentMode.EXECUTE, turn_index=4, iteration=1)
    iteration_repeat = state.instruction_for(AgentMode.EXECUTE, turn_index=5, iteration=4)

    assert first.level == "full"
    assert second.level == "compact"
    assert repeated.level == "medium"
    assert iteration_repeat.level == "medium"


def test_provider_prompt_payload_separates_stable_and_dynamic_text() -> None:
    rendered = PromptBuilder().build(
        PromptContext.from_runtime(
            workspace_root=".",
            cwd=".",
            mode=AgentMode.EXECUTE,
            turn_index=1,
            iteration=1,
        )
    )

    payload = to_provider_prompt_payload(rendered)

    assert payload.stable_cacheable is True
    assert "MonkeyCode" in payload.stable_system_text
    assert all("monkeycode_" in message for message in payload.dynamic_system_messages)


def test_tool_description_enforces_editing_rules() -> None:
    tool = ToolDefinition("edit_file", "Edit file.", {"type": "object"})

    enhanced = enhance_tool_definition(tool)

    assert "MonkeyCode tool rules:" in enhanced.description
    assert "Before modifying a file" in enhanced.description
    assert "exactly once" in enhanced.description


def test_default_prompt_explains_inspection_tool_selection() -> None:
    rendered = PromptBuilder().build(
        PromptContext.from_runtime(
            workspace_root=".",
            mode=AgentMode.EXECUTE,
            turn_index=1,
            iteration=1,
        )
    )

    prompt = rendered.stable_system_text
    assert "find_files only for path discovery" in prompt
    assert "search_code for known text or symbols" in prompt
    assert "read_file only when a known file needs full context" in prompt
    assert "fewest calls needed" in prompt


def test_render_skills_catalog_empty_and_non_empty() -> None:
    assert render_skills_catalog([]) == ""

    rendered = render_skills_catalog([SkillCatalogItem("commit", "Commit changes")])

    assert "## Available Skills" in rendered
    assert "- commit: Commit changes" in rendered
    assert "LoadSkill" in rendered


def test_render_active_skills_block_empty_and_non_empty() -> None:
    assert render_active_skills_block([]) == ""

    rendered = render_active_skills_block([ActiveSkillEntry("commit", "Body")])

    assert "## Active Skills" in rendered
    assert "### Skill: commit" in rendered
    assert "Body" in rendered


def test_prompt_context_includes_dynamic_blocks() -> None:
    rendered = PromptBuilder().build(
        PromptContext.from_runtime(
            workspace_root=".",
            cwd=".",
            mode=AgentMode.EXECUTE,
            turn_index=1,
            iteration=1,
            dynamic_context_blocks=["## Active Skills\n\nBody"],
        )
    )

    assert "## Active Skills" in rendered.dynamic_system_messages[0]
