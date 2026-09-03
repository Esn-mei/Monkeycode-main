from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

from monkeycode.errors import ConfigError
from monkeycode.hooks.matcher import permission_pattern_matches

if TYPE_CHECKING:
    from monkeycode.tools.base import ToolPolicy, ToolResult


class PermissionMode(str, Enum):
    STRICT = "strict"
    DEFAULT = "default"
    ALLOW = "allow"


class PermissionAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionLayer(str, Enum):
    BLACKLIST = "blacklist"
    SANDBOX = "sandbox"
    SESSION = "session"
    LOCAL = "local"
    PROJECT = "project"
    USER = "user"
    MODE = "mode"
    HUMAN = "human"


class HumanDecision(str, Enum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    ALLOW_PERMANENT = "allow_permanent"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionRequest:
    tool_name: str
    arguments: dict[str, Any]
    policy: ToolPolicy
    workspace_root: Path
    mode: PermissionMode
    target: str
    risk_summary: str


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    layer: PermissionLayer
    reason: str
    mode: PermissionMode
    rule: str | None = None
    error_type: str | None = None

    def metadata(self, target: str) -> dict[str, Any]:
        return {
            "permission_decision": self.action.value,
            "permission_layer": self.layer.value,
            "permission_rule": self.rule,
            "permission_mode": self.mode.value,
            "permission_target": target,
        }


@dataclass(frozen=True)
class PermissionAuthorization:
    allowed: bool
    decision: PermissionDecision
    target: str
    denial_result: ToolResult | None = None


class PermissionPrompter(Protocol):
    def prompt(self, request: PermissionRequest, decision: PermissionDecision) -> HumanDecision:
        ...


class DenyPermissionPrompter:
    def prompt(self, request: PermissionRequest, decision: PermissionDecision) -> HumanDecision:
        return HumanDecision.DENY


@dataclass(frozen=True)
class PermissionRule:
    tool_name: str
    pattern: str
    action: PermissionAction
    layer: PermissionLayer
    source: Path | None = None

    @property
    def key(self) -> str:
        return f"{self.tool_name}({self.pattern})"

    def matches(self, tool_name: str, target: str) -> bool:
        if self.tool_name != tool_name:
            return False
        return permission_pattern_matches(self.pattern, target)


@dataclass
class PermissionRuleStore:
    workspace_root: Path
    user_rules: list[PermissionRule] = field(default_factory=list)
    project_rules: list[PermissionRule] = field(default_factory=list)
    local_rules: list[PermissionRule] = field(default_factory=list)
    session_rules: list[PermissionRule] = field(default_factory=list)
    user_path: Path | None = None
    project_path: Path | None = None
    local_path: Path | None = None

    @classmethod
    def empty(cls, workspace_root: Path) -> PermissionRuleStore:
        workspace = workspace_root.resolve()
        return cls(
            workspace_root=workspace,
            user_path=Path.home() / ".monkeycode" / "permissions.yaml",
            project_path=workspace / "monkeycode.permissions.yaml",
            local_path=workspace / ".monkeycode" / "permissions.local.yaml",
        )

    @classmethod
    def load(
        cls,
        workspace_root: Path,
        *,
        user_path: Path | None = None,
        project_path: Path | None = None,
        local_path: Path | None = None,
    ) -> PermissionRuleStore:
        workspace = workspace_root.resolve()
        store = cls(
            workspace_root=workspace,
            user_path=user_path or Path.home() / ".monkeycode" / "permissions.yaml",
            project_path=project_path or workspace / "monkeycode.permissions.yaml",
            local_path=local_path or workspace / ".monkeycode" / "permissions.local.yaml",
        )
        store.user_rules = _load_rules(store.user_path, PermissionLayer.USER)
        store.project_rules = _load_rules(store.project_path, PermissionLayer.PROJECT)
        store.local_rules = _load_rules(store.local_path, PermissionLayer.LOCAL)
        return store

    def lookup(self, tool_name: str, target: str) -> PermissionRule | None:
        for rules in [self.session_rules, self.local_rules, self.project_rules, self.user_rules]:
            for rule in rules:
                if rule.matches(tool_name, target):
                    return rule
        return None

    def add_session_rule(
        self,
        tool_name: str,
        target: str,
        action: PermissionAction = PermissionAction.ALLOW,
    ) -> PermissionRule:
        rule = PermissionRule(
            tool_name=tool_name,
            pattern=target or "*",
            action=action,
            layer=PermissionLayer.SESSION,
        )
        self.session_rules.insert(0, rule)
        return rule

    def write_local_rule(
        self,
        tool_name: str,
        target: str,
        action: PermissionAction = PermissionAction.ALLOW,
    ) -> PermissionRule:
        if self.local_path is None:
            self.local_path = self.workspace_root / ".monkeycode" / "permissions.local.yaml"
        data = _read_permissions_yaml(self.local_path)
        rules = data.setdefault("rules", {})
        if not isinstance(rules, dict):
            raise ConfigError(f"invalid permission config {self.local_path}: rules must be a mapping")

        key = f"{tool_name}({target or '*'})"
        rules[key] = action.value
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )

        rule = PermissionRule(
            tool_name=tool_name,
            pattern=target or "*",
            action=action,
            layer=PermissionLayer.LOCAL,
            source=self.local_path,
        )
        self.local_rules.insert(0, rule)
        return rule


class DangerousCommandGuard:
    _PATTERNS = [
        (
            re.compile(
                r"\brm\b(?=.*-[a-z]*r)(?=.*-[a-z]*f)"
                r"(?=.*\s['\"]?(?:/|/\*|[a-z]:[\\/]*|[a-z]:[\\/]+\*)['\"]?(?:\s|$))"
            ),
            "dangerous recursive deletion of a root path",
        ),
        (
            re.compile(
                r"\b(remove-item|rm|rd|rmdir|del|erase)\b"
                r"(?=.*(?:-recurse\b|-r\b|/s\b))"
                r"(?=.*(?:-force\b|-f\b|/q\b))"
                r"(?=.*(?:[a-z]:[\\/]+(?:windows|users|program files|programdata)?|"
                r"[a-z]:[\\/]*$|%systemroot%|\\windows\b))"
            ),
            "dangerous forced recursive deletion of a system path",
        ),
        (
            re.compile(r"\bformat(?:\.com)?\s+[a-z]:"),
            "dangerous disk format command",
        ),
        (
            re.compile(r"\b(clear-disk|remove-partition|remove-volume|reset-physicaldisk)\b"),
            "dangerous disk or volume mutation command",
        ),
        (
            re.compile(r"\bdiskpart\b"),
            "dangerous diskpart command",
        ),
        (
            re.compile(r"\bmkfs(?:\.[a-z0-9]+)?\b"),
            "dangerous filesystem format command",
        ),
    ]

    def check(self, request: PermissionRequest) -> PermissionDecision | None:
        if request.tool_name != "run_command":
            return None
        command = str(request.arguments.get("command", ""))
        normalized = _normalize_command(command)
        for pattern, reason in self._PATTERNS:
            if pattern.search(normalized):
                return PermissionDecision(
                    action=PermissionAction.DENY,
                    layer=PermissionLayer.BLACKLIST,
                    reason=reason,
                    mode=request.mode,
                    error_type="dangerous_command_denied",
                )
        return None


class PathSandbox:
    _PATH_KEYS_BY_TOOL = {
        "read_file": ("path",),
        "write_file": ("path",),
        "edit_file": ("path",),
    }

    def check(self, request: PermissionRequest) -> PermissionDecision | None:
        from monkeycode.tools.workspace import WorkspaceError, WorkspaceGuard

        keys = self._PATH_KEYS_BY_TOOL.get(request.tool_name, ())
        if not keys:
            return None

        guard = WorkspaceGuard(request.workspace_root)
        for key in keys:
            value = request.arguments.get(key)
            if not isinstance(value, str):
                continue
            try:
                if request.tool_name == "write_file":
                    guard.resolve_writable(value)
                else:
                    guard.resolve(value)
            except WorkspaceError as exc:
                return PermissionDecision(
                    action=PermissionAction.DENY,
                    layer=PermissionLayer.SANDBOX,
                    reason=str(exc),
                    mode=request.mode,
                    error_type="path_outside_workspace",
                )
        return None


class PermissionManager:
    def __init__(
        self,
        *,
        mode: PermissionMode | str = PermissionMode.DEFAULT,
        rule_store: PermissionRuleStore | None = None,
        prompter: PermissionPrompter | None = None,
        dangerous_command_guard: DangerousCommandGuard | None = None,
        path_sandbox: PathSandbox | None = None,
    ) -> None:
        self.mode = PermissionMode(mode)
        self.rule_store = rule_store
        self.prompter = prompter or DenyPermissionPrompter()
        self.dangerous_command_guard = dangerous_command_guard or DangerousCommandGuard()
        self.path_sandbox = path_sandbox or PathSandbox()

    def set_prompter(self, prompter: PermissionPrompter) -> None:
        self.prompter = prompter

    def authorize(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        policy: ToolPolicy,
        workspace_root: Path,
    ) -> PermissionAuthorization:
        if self.rule_store is None:
            self.rule_store = PermissionRuleStore.empty(workspace_root)
        request = PermissionRequest(
            tool_name=tool_name,
            arguments=arguments,
            policy=policy,
            workspace_root=workspace_root.resolve(),
            mode=self.mode,
            target=_target_for_tool(tool_name, arguments, workspace_root),
            risk_summary=_risk_summary(tool_name, policy),
        )

        for hard_check in [self.dangerous_command_guard.check, self.path_sandbox.check]:
            decision = hard_check(request)
            if decision is not None:
                return _denied(request, decision)

        rule = self.rule_store.lookup(tool_name, request.target)
        if rule is not None:
            decision = PermissionDecision(
                action=rule.action,
                layer=rule.layer,
                reason=f"matched permission rule {rule.key}",
                mode=self.mode,
                rule=rule.key,
            )
            if rule.action == PermissionAction.ALLOW:
                return PermissionAuthorization(allowed=True, decision=decision, target=request.target)
            return _denied(request, decision)

        decision = self._mode_decision(request)
        if decision.action == PermissionAction.ALLOW:
            return PermissionAuthorization(allowed=True, decision=decision, target=request.target)
        if decision.action == PermissionAction.DENY:
            return _denied(request, decision)
        return self._ask_human(request, decision)

    def _mode_decision(self, request: PermissionRequest) -> PermissionDecision:
        if self.mode == PermissionMode.ALLOW:
            return PermissionDecision(
                action=PermissionAction.ALLOW,
                layer=PermissionLayer.MODE,
                reason="permission mode allows unmatched tool calls",
                mode=self.mode,
            )
        if request.policy.category == "read":
            return PermissionDecision(
                action=PermissionAction.ALLOW,
                layer=PermissionLayer.MODE,
                reason="read tool allowed by permission mode",
                mode=self.mode,
            )
        return PermissionDecision(
            action=PermissionAction.ASK,
            layer=PermissionLayer.MODE,
            reason="permission mode requires approval for unmatched side-effect tools",
            mode=self.mode,
        )

    def _ask_human(
        self,
        request: PermissionRequest,
        decision: PermissionDecision,
    ) -> PermissionAuthorization:
        try:
            human_decision = self.prompter.prompt(request, decision)
        except (EOFError, OSError):
            human_decision = HumanDecision.DENY
        human_decision = HumanDecision(human_decision)

        if human_decision == HumanDecision.ALLOW_ONCE:
            allow = PermissionDecision(
                action=PermissionAction.ALLOW,
                layer=PermissionLayer.HUMAN,
                reason="approved for this tool call",
                mode=self.mode,
            )
            return PermissionAuthorization(allowed=True, decision=allow, target=request.target)
        if human_decision == HumanDecision.ALLOW_SESSION:
            rule = self.rule_store.add_session_rule(request.tool_name, request.target)
            allow = PermissionDecision(
                action=PermissionAction.ALLOW,
                layer=PermissionLayer.SESSION,
                reason="approved for this session",
                mode=self.mode,
                rule=rule.key,
            )
            return PermissionAuthorization(allowed=True, decision=allow, target=request.target)
        if human_decision == HumanDecision.ALLOW_PERMANENT:
            try:
                rule = self.rule_store.write_local_rule(request.tool_name, request.target)
            except ConfigError as exc:
                denied = PermissionDecision(
                    action=PermissionAction.DENY,
                    layer=PermissionLayer.LOCAL,
                    reason=str(exc),
                    mode=self.mode,
                    error_type="permission_config_error",
                )
                return _denied(request, denied)
            allow = PermissionDecision(
                action=PermissionAction.ALLOW,
                layer=PermissionLayer.LOCAL,
                reason="approved permanently in local permissions",
                mode=self.mode,
                rule=rule.key,
            )
            return PermissionAuthorization(allowed=True, decision=allow, target=request.target)

        denied = PermissionDecision(
            action=PermissionAction.DENY,
            layer=PermissionLayer.HUMAN,
            reason="permission denied by user or non-interactive input",
            mode=self.mode,
            error_type="permission_denied",
        )
        return _denied(request, denied)


def _denied(request: PermissionRequest, decision: PermissionDecision) -> PermissionAuthorization:
    from monkeycode.tools.base import ToolResult

    error_type = decision.error_type or "permission_denied"
    result = ToolResult(
        tool_name=request.tool_name,
        success=False,
        error_type=error_type,
        error_message=decision.reason,
        metadata=decision.metadata(request.target),
    )
    return PermissionAuthorization(allowed=False, decision=decision, target=request.target, denial_result=result)


def _load_rules(path: Path | None, layer: PermissionLayer) -> list[PermissionRule]:
    if path is None or not path.exists():
        return []
    data = _read_permissions_yaml(path)
    raw_rules = data.get("rules", {})
    if raw_rules is None:
        return []
    if not isinstance(raw_rules, dict):
        raise ConfigError(f"invalid permission config {path}: rules must be a mapping")
    rules: list[PermissionRule] = []
    for key, action in raw_rules.items():
        if not isinstance(key, str):
            raise ConfigError(f"invalid permission rule in {path}: rule key must be a string")
        if not isinstance(action, str) or action not in {PermissionAction.ALLOW.value, PermissionAction.DENY.value}:
            raise ConfigError(f"invalid permission rule {key!r} in {path}: value must be allow or deny")
        tool_name, pattern = _parse_rule_key(key, path)
        rules.append(
            PermissionRule(
                tool_name=tool_name,
                pattern=pattern,
                action=PermissionAction(action),
                layer=layer,
                source=path,
            )
        )
    return rules


def _read_permissions_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"rules": {}}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid permission config {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read permission config {path}: {exc}") from exc
    if data is None:
        return {"rules": {}}
    if not isinstance(data, dict):
        raise ConfigError(f"invalid permission config {path}: expected a mapping")
    return data


def _parse_rule_key(key: str, path: Path) -> tuple[str, str]:
    match = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)\((.*)\)\s*", key)
    if not match:
        raise ConfigError(f"invalid permission rule {key!r} in {path}: expected tool(pattern)")
    tool_name = match.group(1).strip()
    pattern = match.group(2).strip()
    if not tool_name or not pattern:
        raise ConfigError(f"invalid permission rule {key!r} in {path}: tool and pattern are required")
    return tool_name, pattern


def _target_for_tool(tool_name: str, arguments: dict[str, Any], workspace_root: Path) -> str:
    if tool_name == "run_command":
        return str(arguments.get("command", "")).strip()
    if tool_name in {"read_file", "write_file", "edit_file"}:
        from monkeycode.tools.workspace import WorkspaceError, WorkspaceGuard

        path = arguments.get("path", "")
        if isinstance(path, str):
            try:
                return WorkspaceGuard(workspace_root).relative(path)
            except (WorkspaceError, OSError, ValueError):
                return path.replace("\\", "/")
    if tool_name == "find_files":
        return str(arguments.get("pattern", "")).strip()
    if tool_name == "search_code":
        return str(arguments.get("path_pattern") or arguments.get("query") or "").strip()
    for key in ["path", "pattern", "query", "command"]:
        value = arguments.get(key)
        if isinstance(value, str):
            return value.strip()
    return "*"


def _risk_summary(tool_name: str, policy: ToolPolicy) -> str:
    if tool_name == "run_command":
        return "runs a shell command in the workspace"
    if tool_name == "write_file":
        return "creates or overwrites a workspace file"
    if tool_name == "edit_file":
        return "modifies a workspace file"
    if policy.has_side_effects:
        return "may change workspace state"
    return "read-only workspace access"


def _normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip().lower())


def _normalize_match_value(value: str) -> str:
    return value.strip().replace("\\", "/").lower()


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")
