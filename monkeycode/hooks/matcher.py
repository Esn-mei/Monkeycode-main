from __future__ import annotations

import dataclasses
import fnmatch
import re
from typing import Any

from monkeycode.hooks.types import HookCondition, HookEventContext, HookMatchClause


MISSING = object()


def get_field_value(data: Any, field_path: str) -> Any:
    current = data.data if isinstance(data, HookEventContext) else data
    for part in field_path.split("."):
        if not part:
            return MISSING
        if isinstance(current, dict):
            if part not in current:
                return MISSING
            current = current[part]
            continue
        if dataclasses.is_dataclass(current):
            if not hasattr(current, part):
                return MISSING
            current = getattr(current, part)
            continue
        if isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return MISSING
            current = current[index]
            continue
        if not hasattr(current, part):
            return MISSING
        current = getattr(current, part)
    return current


def match_text(candidate: Any, pattern: str, *, match: str = "exact", negate: bool = False) -> bool:
    text = _normalize(candidate)
    expected = _normalize(pattern)
    matched = _match_normalized(text, expected, match)
    return not matched if negate else matched


def match_clause(clause: HookMatchClause, context: HookEventContext) -> bool:
    value = get_field_value(context, clause.field)
    if value is MISSING:
        return False
    return match_text(value, clause.value, match=clause.match, negate=clause.negate)


def match_condition(condition: HookCondition | None, context: HookEventContext) -> bool:
    if condition is None:
        return True
    if not condition.clauses:
        return True
    results = [match_clause(clause, context) for clause in condition.clauses]
    if condition.mode == "all":
        return all(results)
    return any(results)


def permission_pattern_matches(pattern: str, candidate: str) -> bool:
    normalized_pattern = _normalize(pattern)
    normalized_candidate = _normalize(candidate)
    match_kind = "glob" if _has_glob(normalized_pattern) else "exact"
    return _match_normalized(normalized_candidate, normalized_pattern, match_kind)


def _match_normalized(candidate: str, pattern: str, match: str) -> bool:
    if match == "exact":
        return candidate == pattern
    if match == "glob":
        return fnmatch.fnmatchcase(candidate, pattern)
    if match == "regex":
        return re.search(pattern, candidate) is not None
    return False


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")
