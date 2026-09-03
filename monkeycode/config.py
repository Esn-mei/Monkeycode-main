from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from monkeycode.errors import ConfigError, UnsupportedProtocolError


ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class SecretValue:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue(***)"

    def __str__(self) -> str:
        return "***"


@dataclass(frozen=True)
class ContextConfig:
    enabled: bool = True
    context_window_tokens: int = 32000
    auto_safety_margin_tokens: int = 13000
    manual_safety_margin_tokens: int = 3000
    recent_tail_tokens: int = 10000
    recent_tail_min_messages: int = 5
    single_tool_result_tokens: int = 6000
    turn_tool_results_tokens: int = 12000
    archive_dir: str = ".monkeycode/context"


@dataclass(frozen=True)
class WorktreeConfig:
    enabled: bool = True
    root: str = ".monkeycode/worktrees"
    ttl_hours: int = 24
    copy_paths: tuple[str, ...] = ()
    link_dirs: tuple[str, ...] = ()
    include_ignored: tuple[str, ...] = ()
    hooks_path: str | None = None


@dataclass(frozen=True)
class AppConfig:
    protocol: Literal["anthropic", "openai"] | str
    model: str
    base_url: str
    api_key: SecretValue
    options: dict[str, Any] = field(default_factory=dict)
    context: ContextConfig = field(default_factory=ContextConfig)
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    enable_subagent_background: bool | None = None

    def __repr__(self) -> str:
        return (
            "AppConfig("
            f"protocol={self.protocol!r}, "
            f"model={self.model!r}, "
            f"base_url={self.base_url!r}, "
            "api_key=***, "
            f"options={self.options!r}, "
            f"context={self.context!r}, "
            f"worktree={self.worktree!r}, "
            f"enable_subagent_background={self.enable_subagent_background!r})"
        )

    __str__ = __repr__

    def effective_enable_subagent_background(self) -> bool:
        if self.enable_subagent_background is None:
            return True
        return self.enable_subagent_background


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    _load_dotenv(config_path.parent / ".env")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML config: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config must be a YAML mapping")

    required = ["protocol", "model", "base_url", "api_key"]
    for field_name in required:
        value = raw.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ConfigError(f"missing required config field: {field_name}")

    protocol = str(raw["protocol"]).strip()
    if protocol not in {"anthropic", "openai"}:
        raise UnsupportedProtocolError(
            f"unsupported protocol {protocol!r}; supported values: anthropic, openai"
        )

    options = raw.get("options", {})
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ConfigError("options must be a YAML mapping when provided")
    context = _parse_context_config(raw.get("context", {}))
    worktree = _parse_worktree_config(raw.get("worktree", {}))
    enable_subagent_background = raw.get("enableSubAgentBackground")
    if enable_subagent_background is not None and not isinstance(enable_subagent_background, bool):
        raise ConfigError("enableSubAgentBackground must be a boolean")

    api_key = _expand_api_key(str(raw["api_key"]).strip(), config_path)

    return AppConfig(
        protocol=protocol,
        model=str(raw["model"]).strip(),
        base_url=str(raw["base_url"]).strip().rstrip("/"),
        api_key=SecretValue(api_key),
        options=dict(options),
        context=context,
        worktree=worktree,
        enable_subagent_background=enable_subagent_background,
    )


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"failed to read dotenv file {path}: {exc}") from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"invalid dotenv entry at {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ConfigError(f"invalid dotenv variable name at {path}:{line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            # A project-local .env is the configuration explicitly selected by
            # this config file. Let it replace stale inherited shell values.
            os.environ[name] = value


def _expand_api_key(value: str, config_path: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.environ.get(name)
        if resolved is None:
            raise ConfigError(
                f"missing environment variable {name!r} referenced by config {config_path}"
            )
        return resolved

    expanded = ENV_VAR_PATTERN.sub(replace, value).strip()
    if not expanded:
        raise ConfigError("api_key must resolve to a non-empty value")
    return expanded


def _parse_context_config(raw: Any) -> ContextConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("context must be a YAML mapping when provided")

    defaults = ContextConfig()
    values: dict[str, Any] = {
        field_name: getattr(defaults, field_name)
        for field_name in ContextConfig.__dataclass_fields__
    }

    if "enabled" in raw:
        if not isinstance(raw["enabled"], bool):
            raise ConfigError("context.enabled must be a boolean")
        values["enabled"] = raw["enabled"]

    integer_fields = [
        "context_window_tokens",
        "auto_safety_margin_tokens",
        "manual_safety_margin_tokens",
        "recent_tail_tokens",
        "recent_tail_min_messages",
        "single_tool_result_tokens",
        "turn_tool_results_tokens",
    ]
    for field_name in integer_fields:
        if field_name not in raw:
            continue
        value = raw[field_name]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ConfigError(f"context.{field_name} must be a positive integer")
        values[field_name] = value

    if "archive_dir" in raw:
        archive_dir = raw["archive_dir"]
        if not isinstance(archive_dir, str) or not archive_dir.strip():
            raise ConfigError("context.archive_dir must be a non-empty string")
        values["archive_dir"] = archive_dir.strip()

    if values["context_window_tokens"] <= values["auto_safety_margin_tokens"]:
        raise ConfigError(
            "context.context_window_tokens must be greater than context.auto_safety_margin_tokens"
        )
    if values["context_window_tokens"] <= values["manual_safety_margin_tokens"]:
        raise ConfigError(
            "context.context_window_tokens must be greater than context.manual_safety_margin_tokens"
        )
    if values["turn_tool_results_tokens"] < values["single_tool_result_tokens"]:
        raise ConfigError(
            "context.turn_tool_results_tokens must be greater than or equal to context.single_tool_result_tokens"
        )

    return ContextConfig(**values)


def _parse_worktree_config(raw: Any) -> WorktreeConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("worktree must be a YAML mapping")
    defaults = WorktreeConfig()
    enabled = raw.get("enabled", defaults.enabled)
    root = raw.get("root", defaults.root)
    ttl_hours = raw.get("ttl_hours", defaults.ttl_hours)
    hooks_path = raw.get("hooks_path", defaults.hooks_path)
    if not isinstance(enabled, bool):
        raise ConfigError("worktree.enabled must be a boolean")
    if not isinstance(root, str) or not root.strip():
        raise ConfigError("worktree.root must be a non-empty relative path")
    if Path(root).is_absolute() or ".." in Path(root).parts or ":" in root:
        raise ConfigError("worktree.root must stay inside the repository")
    if not isinstance(ttl_hours, int) or isinstance(ttl_hours, bool) or ttl_hours <= 0:
        raise ConfigError("worktree.ttl_hours must be a positive integer")
    if hooks_path is not None and (not isinstance(hooks_path, str) or not hooks_path.strip()):
        raise ConfigError("worktree.hooks_path must be null or a non-empty relative path")

    def paths(key: str) -> tuple[str, ...]:
        value = raw.get(key, [])
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ConfigError(f"worktree.{key} must be a list of non-empty strings")
        return tuple(value)

    return WorktreeConfig(
        enabled=enabled,
        root=root.strip(),
        ttl_hours=ttl_hours,
        copy_paths=paths("copy_paths"),
        link_dirs=paths("link_dirs"),
        include_ignored=paths("include_ignored"),
        hooks_path=hooks_path.strip() if hooks_path else None,
    )
