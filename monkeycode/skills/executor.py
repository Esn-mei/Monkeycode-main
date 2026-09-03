from __future__ import annotations

import inspect

from monkeycode.skills.catalog import Catalog
from monkeycode.skills.parser import read_skill_body
from monkeycode.skills.render import render_body, with_body


class Executor:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    async def execute(self, ui, name: str, args: str = "") -> None:
        skill = self.catalog.get(name)
        if skill is None:
            ui.error(f"skill not found: {name}")
            return

        fresh_skill = with_body(skill, read_skill_body(skill))
        rendered = render_body(fresh_skill, args)
        if not fresh_skill.meta.is_fork():
            result = ui.inject_and_send(f"/{name}", rendered)
            if inspect.isawaitable(result):
                await result
            return

        try:
            final_text = ui.run_fork_skill(
                name,
                rendered,
                fresh_skill.meta.allowed_tools,
                fresh_skill.meta.fork_context,
                fresh_skill.meta.model,
            )
            if inspect.isawaitable(final_text):
                final_text = await final_text
        except BaseException as exc:
            final_text = f"[skill {name} failed: {exc}]"

        appended = ui.append_assistant_message(str(final_text))
        if inspect.isawaitable(appended):
            await appended
