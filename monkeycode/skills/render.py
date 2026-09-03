from __future__ import annotations

from dataclasses import replace

from monkeycode.skills.types import Skill


def render_body(skill: Skill, args: str) -> str:
    body = skill.prompt_body
    if "$ARGUMENTS" in body:
        body = body.replace("$ARGUMENTS", args)
    elif args.strip():
        body = f"{body.rstrip()}\n\n## User Request\n\n{args}"

    if skill.meta.allowed_tools:
        tools = ", ".join(skill.meta.allowed_tools)
        prefix = (
            "This skill is designed to use only these tools: "
            f"{tools}. Prefer them over other tools when possible.\n\n---\n\n"
        )
        body = prefix + body
    return body


def with_body(skill: Skill, body: str) -> Skill:
    return replace(skill, prompt_body=body)
