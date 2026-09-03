from __future__ import annotations


async def handle_skill(ui) -> None:
    skills = sorted(ui.list_catalog_skills(), key=lambda item: item.name)
    if not skills:
        ui.println("No skills loaded.")
        return
    width = max(len(item.name) for item in skills)
    ui.println(f"Available skills ({len(skills)}):")
    for item in skills:
        ui.println(f" /{item.name.ljust(width)} {item.description}")
    ui.println("Type /<skill-name> to invoke a skill.")
