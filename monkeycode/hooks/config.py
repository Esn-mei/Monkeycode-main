from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from monkeycode.hooks.types import (
    SUPPORTED_EVENTS,
    HookActionSpec,
    HookConfig,
    HookCondition,
    HookExecutionControl,
    HookMatchClause,
    HookRule,
    ValidationIssue,
)

ACTION_TYPES = {"command", "prompt", "http", "subagent"}
MATCH_TYPES = {"exact", "glob", "regex"}


def default_user_config_path() -> Path:
    return Path.home() / ".monkeycode" / "hooks.yaml"


def default_project_config_path(workspace_root: Path) -> Path:
    return workspace_root.resolve() / "monkeycode.hooks.yaml"


def default_local_config_path(workspace_root: Path) -> Path:
    return workspace_root.resolve() / ".monkeycode" / "hooks.local.yaml"


def load_hook_config(
    workspace_root: str | Path,
    *,
    user_path: str | Path | None = None,
    project_path: str | Path | None = None,
    local_path: str | Path | None = None,
) -> HookConfig:
    workspace = Path(workspace_root).resolve()
    paths = [
        Path(user_path) if user_path is not None else default_user_config_path(),
        Path(project_path) if project_path is not None else default_project_config_path(workspace),
        Path(local_path) if local_path is not None else default_local_config_path(workspace),
    ]
    rules: list[HookRule] = []
    issues: list[ValidationIssue] = []
    for path in paths:
        loaded, loaded_issues = _load_one(path)
        rules.extend(loaded)
        issues.extend(loaded_issues)
    return HookConfig(rules=rules, issues=issues)


def _load_one(path: Path) -> tuple[list[HookRule], list[ValidationIssue]]:
    if not path.exists():
        return [], []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [], [ValidationIssue(f"invalid Hook config YAML: {exc}", source_path=path)]
    except OSError as exc:
        return [], [ValidationIssue(f"cannot read Hook config: {exc}", source_path=path)]

    if raw is None:
        return [], []
    if not isinstance(raw, dict):
        return [], [ValidationIssue("invalid Hook config: expected a mapping", source_path=path)]
    raw_hooks = raw.get("hooks", [])
    if raw_hooks is None:
        return [], []
    if not isinstance(raw_hooks, list):
        return [], [ValidationIssue("invalid Hook config: hooks must be a list", source_path=path, field="hooks")]

    rules: list[HookRule] = []
    issues: list[ValidationIssue] = []
    for index, raw_rule in enumerate(raw_hooks):
        rule, rule_issues = _parse_rule(raw_rule, path, index)
        if rule is not None:
            rules.append(rule)
        issues.extend(rule_issues)
    return rules, issues


def _parse_rule(raw_rule: Any, path: Path, index: int) -> tuple[HookRule | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    if not isinstance(raw_rule, dict):
        return None, [_issue("rule must be a mapping", path, index)]

    event = raw_rule.get("event")
    if not isinstance(event, str) or not event.strip():
        issues.append(_issue("event is required", path, index, field="event"))
    elif event not in SUPPORTED_EVENTS:
        issues.append(_issue(f"unsupported event {event!r}", path, index, field="event", event=event))

    action, action_issues = _parse_action(raw_rule.get("action"), path, index, event if isinstance(event, str) else None)
    issues.extend(action_issues)

    condition, condition_issues = _parse_condition(raw_rule.get("if"), path, index)
    issues.extend(condition_issues)

    control, control_issues = _parse_control(raw_rule.get("control"), raw_rule, path, index)
    issues.extend(control_issues)

    if action is not None and action.type == "prompt" and action.target == "tool_result":
        if event != "tool.before":
            issues.append(
                _issue(
                    "prompt target tool_result is only allowed on tool.before",
                    path,
                    index,
                    field="action.target",
                    event=event if isinstance(event, str) else None,
                    action_type=action.type,
                )
            )
        if control.background:
            issues.append(
                _issue(
                    "intercept rules cannot run in background",
                    path,
                    index,
                    field="control.background",
                    event=event if isinstance(event, str) else None,
                    action_type=action.type,
                )
            )

    if issues or action is None or not isinstance(event, str):
        return None, issues

    rule_id = raw_rule.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        rule_id = f"{path.name}:{index}"

    return (
        HookRule(
            id=rule_id.strip(),
            event=event,
            condition=condition,
            action=action,
            control=control,
            source_path=path,
            source_index=index,
        ),
        [],
    )


def _parse_condition(raw: Any, path: Path, index: int) -> tuple[HookCondition | None, list[ValidationIssue]]:
    if raw is None or raw == {}:
        return None, []
    if not isinstance(raw, dict):
        return None, [_issue("if must be a mapping", path, index, field="if")]

    has_all = "all" in raw
    has_any = "any" in raw
    if has_all and has_any:
        return None, [_issue("if cannot declare both all and any", path, index, field="if")]
    if not has_all and not has_any:
        return None, [_issue("if must declare all or any", path, index, field="if")]

    mode = "all" if has_all else "any"
    raw_clauses = raw.get(mode)
    if raw_clauses is None:
        return HookCondition(mode=mode, clauses=[]), []
    if not isinstance(raw_clauses, list):
        return None, [_issue(f"if.{mode} must be a list", path, index, field=f"if.{mode}")]

    clauses: list[HookMatchClause] = []
    issues: list[ValidationIssue] = []
    for clause_index, raw_clause in enumerate(raw_clauses):
        clause, clause_issues = _parse_clause(raw_clause, path, index, f"if.{mode}[{clause_index}]")
        if clause is not None:
            clauses.append(clause)
        issues.extend(clause_issues)
    if issues:
        return None, issues
    return HookCondition(mode=mode, clauses=clauses), []


def _parse_clause(raw: Any, path: Path, index: int, field: str) -> tuple[HookMatchClause | None, list[ValidationIssue]]:
    if not isinstance(raw, dict):
        return None, [_issue("condition clause must be a mapping", path, index, field=field)]
    clause_field = raw.get("field")
    if not isinstance(clause_field, str) or not clause_field.strip():
        return None, [_issue("condition field is required", path, index, field=f"{field}.field")]
    if "value" not in raw:
        return None, [_issue("condition value is required", path, index, field=f"{field}.value")]
    value = raw.get("value")
    match = raw.get("match", raw.get("op", "exact"))
    if not isinstance(match, str) or match not in MATCH_TYPES:
        return None, [_issue("condition match must be exact, glob, or regex", path, index, field=f"{field}.match")]
    text_value = str(value)
    if match == "regex":
        try:
            re.compile(text_value)
        except re.error as exc:
            return None, [_issue(f"invalid regex {text_value!r}: {exc}", path, index, field=f"{field}.value")]
    negate = raw.get("negate", raw.get("not", False))
    if not isinstance(negate, bool):
        return None, [_issue("condition negate must be boolean", path, index, field=f"{field}.negate")]
    return HookMatchClause(field=clause_field.strip(), value=text_value, match=match, negate=negate), []


def _parse_action(raw: Any, path: Path, index: int, event: str | None) -> tuple[HookActionSpec | None, list[ValidationIssue]]:
    if not isinstance(raw, dict):
        return None, [_issue("action is required and must be a mapping", path, index, field="action", event=event)]
    action_type = raw.get("type")
    if not isinstance(action_type, str) or not action_type.strip():
        return None, [_issue("action.type is required", path, index, field="action.type", event=event)]
    if action_type not in ACTION_TYPES:
        return None, [
            _issue(
                f"unsupported action type {action_type!r}",
                path,
                index,
                field="action.type",
                event=event,
                action_type=action_type,
            )
        ]
    params = {key: value for key, value in raw.items() if key != "type"}
    issue = _validate_action_params(action_type, params, path, index, event)
    if issue is not None:
        return None, [issue]
    return HookActionSpec(type=action_type, params=params), []


def _validate_action_params(
    action_type: str,
    params: dict[str, Any],
    path: Path,
    index: int,
    event: str | None,
) -> ValidationIssue | None:
    if action_type == "command":
        if not isinstance(params.get("command"), str) or not params.get("command", "").strip():
            return _issue("command action requires command", path, index, field="action.command", event=event, action_type=action_type)
    if action_type == "prompt":
        content = params.get("content", params.get("reason"))
        if not isinstance(content, str) or not content.strip():
            return _issue("prompt action requires content or reason", path, index, field="action.content", event=event, action_type=action_type)
    if action_type == "http":
        url = params.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return _issue("http action requires http:// or https:// url", path, index, field="action.url", event=event, action_type=action_type)
    if action_type == "subagent":
        prompt = params.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return _issue(
                "subagent action requires prompt",
                path,
                index,
                field="action.prompt",
                event=event,
                action_type=action_type,
            )
        if event in {"system.config_loaded", "system.hooks_loaded"}:
            return _issue(
                f"subagent action is unavailable during {event}",
                path,
                index,
                field="event",
                event=event,
                action_type=action_type,
            )
        for key in ("subagent_type", "name", "model"):
            value = params.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                return _issue(
                    f"subagent action {key} must be a non-empty string",
                    path,
                    index,
                    field=f"action.{key}",
                    event=event,
                    action_type=action_type,
                )
        isolation = params.get("isolation")
        if isolation is not None and isolation not in {"none", "worktree"}:
            return _issue(
                "subagent action isolation must be none or worktree",
                path,
                index,
                field="action.isolation",
                event=event,
                action_type=action_type,
            )
    return None


def _parse_control(
    raw_control: Any,
    raw_rule: dict[str, Any],
    path: Path,
    index: int,
) -> tuple[HookExecutionControl, list[ValidationIssue]]:
    merged: dict[str, Any] = {}
    if raw_control is not None:
        if not isinstance(raw_control, dict):
            return HookExecutionControl(), [_issue("control must be a mapping", path, index, field="control")]
        merged.update(raw_control)
    for key in ("once", "background", "timeout_seconds"):
        if key in raw_rule:
            merged[key] = raw_rule[key]

    issues: list[ValidationIssue] = []
    once = merged.get("once", False)
    background = merged.get("background", False)
    timeout = merged.get("timeout_seconds")

    if not isinstance(once, bool):
        issues.append(_issue("once must be boolean", path, index, field="control.once"))
        once = False
    if not isinstance(background, bool):
        issues.append(_issue("background must be boolean", path, index, field="control.background"))
        background = False
    timeout_seconds: float | None = None
    if timeout is not None:
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
            timeout_seconds = float(timeout)
        else:
            issues.append(_issue("timeout_seconds must be a positive number", path, index, field="control.timeout_seconds"))
    return HookExecutionControl(once=once, background=background, timeout_seconds=timeout_seconds), issues


def _issue(
    message: str,
    path: Path,
    index: int,
    *,
    field: str | None = None,
    event: str | None = None,
    action_type: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        message=message,
        source_path=path,
        source_index=index,
        field=field,
        event=event,
        action_type=action_type,
    )
