from __future__ import annotations

from monkeycode import prompts
from monkeycode.events import AgentMode


async def handle_do(ui) -> None:
    ui.set_mode(AgentMode.EXECUTE)
    ui.inject_and_send("/do", prompts.EXECUTE_DIRECTIVE)


async def handle_review(ui) -> None:
    ui.inject_and_send("/review", prompts.REVIEW_DIRECTIVE)
