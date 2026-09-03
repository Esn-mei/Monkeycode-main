from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


HookEventName = Literal[
    "session.started",
    "session.ended",
    "session.resumed",
    "session.cleared",
    "turn.started",
    "turn.completed",
    "turn.error",
    "message.user_received",
    "message.assistant_completed",
    "message.prompt_before_build",
    "message.prompt_after_build",
    "tool.before",
    "tool.after",
    "tool.error",
    "system.config_loaded",
    "system.hooks_loaded",
    "system.context_before_compact",
    "system.context_after_compact",
]

SUPPORTED_EVENTS: set[str] = {
    "session.started",
    "session.ended",
    "session.resumed",
    "session.cleared",
    "turn.started",
    "turn.completed",
    "turn.error",
    "message.user_received",
    "message.assistant_completed",
    "message.prompt_before_build",
    "message.prompt_after_build",
    "tool.before",
    "tool.after",
    "tool.error",
    "system.config_loaded",
    "system.hooks_loaded",
    "system.context_before_compact",
    "system.context_after_compact",
}

MatchKind = Literal["exact", "glob", "regex"]
ConditionMode = Literal["all", "any"]
ActionType = Literal["command", "prompt", "http", "subagent"]
PromptTarget = Literal["next_prompt", "turn_context", "session_context", "tool_result"]


@dataclass(frozen=True)
class HookMatchClause:
    field: str
    value: str
    match: MatchKind = "exact"
    negate: bool = False


@dataclass(frozen=True)
class HookCondition:
    mode: ConditionMode
    clauses: list[HookMatchClause] = field(default_factory=list)


@dataclass(frozen=True)
class HookActionSpec:
    type: ActionType
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def target(self) -> str:
        value = self.params.get("target")
        return value if isinstance(value, str) and value else "next_prompt"


@dataclass(frozen=True)
class HookExecutionControl:
    once: bool = False
    background: bool = False
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class HookRule:
    id: str
    event: str
    action: HookActionSpec
    condition: HookCondition | None = None
    control: HookExecutionControl = field(default_factory=HookExecutionControl)
    source_path: Path | None = None
    source_index: int = 0


@dataclass(frozen=True)
class ValidationIssue:
    message: str
    source_path: Path | None = None
    source_index: int | None = None
    field: str | None = None
    event: str | None = None
    action_type: str | None = None

    def format(self) -> str:
        parts: list[str] = []
        if self.source_path is not None:
            parts.append(str(self.source_path))
        if self.source_index is not None:
            parts.append(f"rule[{self.source_index}]")
        if self.field:
            parts.append(self.field)
        prefix = ": ".join(parts)
        return f"{prefix}: {self.message}" if prefix else self.message


@dataclass(frozen=True)
class HookConfig:
    rules: list[HookRule] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.rules


@dataclass(frozen=True)
class HookEventContext:
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_values(cls, **values: Any) -> HookEventContext:
        return cls(data=dict(values))


@dataclass(frozen=True)
class HookActionResult:
    success: bool
    action_type: str
    output: dict[str, Any] | str | None = None
    error_message: str | None = None
    prompt_target: str | None = None
    prompt_content: str | None = None
    duration_ms: int | None = None
    background: bool = False


@dataclass(frozen=True)
class HookToolDecision:
    allowed: bool = True
    reason: str | None = None
    rule_id: str | None = None

    @classmethod
    def allow(cls) -> HookToolDecision:
        return cls(allowed=True)

    @classmethod
    def block(cls, reason: str, rule_id: str) -> HookToolDecision:
        return cls(allowed=False, reason=reason, rule_id=rule_id)


@dataclass(frozen=True)
class HookDispatchResult:
    event: str
    matched_rules: list[str] = field(default_factory=list)
    action_results: list[HookActionResult] = field(default_factory=list)
    tool_decision: HookToolDecision = field(default_factory=HookToolDecision.allow)
