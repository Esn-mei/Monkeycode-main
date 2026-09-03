from __future__ import annotations

from monkeycode.events import AgentMode


async def handle_exit(ui) -> None:
    ui.quit()


async def handle_plan(ui) -> None:
    ui.set_mode(AgentMode.PLAN)
    ui.println("已切换到 PLAN 模式")


async def handle_compact(ui) -> None:
    if not ui.idle():
        ui.error("请等待当前任务完成")
        return
    ui.force_compact()


async def handle_resume(ui) -> None:
    if not ui.idle():
        ui.error("请等待当前任务完成")
        return
    ui.open_resume_menu()


async def handle_clear(ui) -> None:
    if not ui.idle():
        ui.error("请等待当前任务完成")
        return
    ui.clear_and_new_session()
    ui.clear_active_skills()


async def handle_cancel(ui) -> None:
    ui.cancel()


async def handle_default(ui) -> None:
    ui.set_mode(AgentMode.EXECUTE)
    ui.println("已切换到 EXECUTE 模式")
