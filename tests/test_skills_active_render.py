from __future__ import annotations

from monkeycode.skills.active import ActiveSkills
from monkeycode.skills.render import render_body
from monkeycode.skills.types import Skill, SkillMeta, SkillSource


def make_skill(body: str, *, allowed_tools: list[str] | None = None) -> Skill:
    return Skill(
        meta=SkillMeta(
            name="demo",
            description="Demo",
            allowed_tools=allowed_tools or [],
        ),
        prompt_body=body,
        source_dir=__import__("pathlib").Path("."),
        source=SkillSource.USER,
    )


def test_active_skills_activate_update_and_clear() -> None:
    active = ActiveSkills()

    active.activate("one", "body1")
    active.activate("two", "body2")
    active.activate("one", "body1b")

    assert active.names() == ["one", "two"]
    assert [entry.body for entry in active.snapshot()] == ["body1b", "body2"]
    active.clear()
    assert active.names() == []


def test_render_body_replaces_arguments() -> None:
    rendered = render_body(make_skill("Hello $ARGUMENTS"), "world")

    assert rendered == "Hello world"


def test_render_body_appends_arguments_without_placeholder() -> None:
    rendered = render_body(make_skill("Hello"), "world")

    assert rendered == "Hello\n\n## User Request\n\nworld"


def test_render_body_adds_allowed_tools_hint() -> None:
    rendered = render_body(make_skill("Hello", allowed_tools=["read_file"]), "")

    assert rendered.startswith(
        "This skill is designed to use only these tools: read_file."
    )
    assert "---\n\nHello" in rendered
