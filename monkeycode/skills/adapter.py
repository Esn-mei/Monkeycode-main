from __future__ import annotations

from monkeycode.prompting import ActiveSkillEntry, SkillCatalogItem
from monkeycode.skills.active import ActiveSkills
from monkeycode.skills.catalog import Catalog


def catalog_to_prompt_items(catalog: Catalog) -> list[SkillCatalogItem]:
    return [
        SkillCatalogItem(name=skill.meta.name, description=skill.meta.description)
        for skill in catalog.list()
    ]


def active_to_prompt_entries(active: ActiveSkills) -> list[ActiveSkillEntry]:
    return [
        ActiveSkillEntry(name=entry.name, body=entry.body)
        for entry in active.snapshot()
    ]
