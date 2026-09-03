from __future__ import annotations

from monkeycode.hooks.matcher import get_field_value, match_condition, match_text
from monkeycode.hooks.types import HookCondition, HookEventContext, HookMatchClause


def test_match_text_supports_exact_glob_regex_and_negate() -> None:
    assert match_text("run_command", "run_command", match="exact")
    assert match_text("src/app.py", "src/*.py", match="glob")
    assert match_text("python -c print(1)", r"python\s+-c", match="regex")
    assert match_text("read_file", "run_command", match="exact", negate=True)


def test_get_field_value_reads_nested_dicts_and_lists() -> None:
    context = HookEventContext({"tool": {"arguments": {"items": [{"path": "a.py"}]}}})

    assert get_field_value(context, "tool.arguments.items.0.path") == "a.py"


def test_match_condition_all_any_and_missing_fields() -> None:
    context = HookEventContext(
        {
            "tool": {"name": "run_command", "arguments": {"command": "python -c print(1)"}},
            "mode": "execute",
        }
    )

    assert match_condition(
        HookCondition(
            mode="all",
            clauses=[
                HookMatchClause("tool.name", "run_command"),
                HookMatchClause("tool.arguments.command", r"python\s+-c", match="regex"),
            ],
        ),
        context,
    )
    assert match_condition(
        HookCondition(
            mode="any",
            clauses=[
                HookMatchClause("tool.name", "missing"),
                HookMatchClause("mode", "execute"),
            ],
        ),
        context,
    )
    assert not match_condition(
        HookCondition(mode="all", clauses=[HookMatchClause("tool.arguments.missing", "*", match="glob")]),
        context,
    )
