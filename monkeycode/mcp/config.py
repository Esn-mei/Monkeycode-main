from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from monkeycode.errors import ConfigError

SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    source_path: Path | None = None


@dataclass(frozen=True)
class McpConfig:
    servers: dict[str, McpServerConfig] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.servers


def default_user_config_path() -> Path:
    return Path.home() / ".monkeycode" / "mcp.yaml"


def default_project_config_path(workspace_root: Path) -> Path:
    return workspace_root.resolve() / "monkeycode.mcp.yaml"


def load_mcp_config(
    workspace_root: str | Path,
    *,
    user_path: str | Path | None = None,
    project_path: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> McpConfig:
    workspace = Path(workspace_root).resolve()
    env = os.environ if environ is None else environ
    paths = [
        Path(user_path) if user_path is not None else default_user_config_path(),
        Path(project_path) if project_path is not None else default_project_config_path(workspace),
    ]
    servers: dict[str, McpServerConfig] = {}
    for path in paths:
        for name, server in _load_one_config(path, env).items():
            servers[name] = server
    return McpConfig(servers=servers)


def _load_one_config(path: Path, environ: dict[str, str]) -> dict[str, McpServerConfig]:
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid MCP config {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read MCP config {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"invalid MCP config {path}: expected a mapping")
    raw_servers = raw.get("mcp_servers", {})
    if raw_servers is None:
        return {}
    if not isinstance(raw_servers, dict):
        raise ConfigError(f"invalid MCP config {path}: mcp_servers must be a mapping")

    servers: dict[str, McpServerConfig] = {}
    for name, value in raw_servers.items():
        if not isinstance(name, str) or not SERVER_NAME_RE.fullmatch(name):
            raise ConfigError(
                f"invalid MCP server name in {path}: {name!r}; "
                "use letters, numbers, '_' or '-'"
            )
        if not isinstance(value, dict):
            raise ConfigError(f"invalid MCP server {name!r} in {path}: expected a mapping")
        servers[name] = _parse_server(name, value, path, environ)
    return servers


def _parse_server(
    name: str,
    raw: dict[str, Any],
    path: Path,
    environ: dict[str, str],
) -> McpServerConfig:
    transport = raw.get("transport")
    if transport not in {"stdio", "http"}:
        raise ConfigError(
            f"invalid MCP server {name!r} in {path}: transport must be stdio or http"
        )

    if transport == "stdio":
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ConfigError(f"invalid MCP server {name!r} in {path}: command is required")
        args = raw.get("args", [])
        if args is None:
            args = []
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ConfigError(f"invalid MCP server {name!r} in {path}: args must be a string list")
        env = raw.get("env", {})
        if env is None:
            env = {}
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in env.items()
        ):
            raise ConfigError(f"invalid MCP server {name!r} in {path}: env must be a string mapping")
        return McpServerConfig(
            name=name,
            transport="stdio",
            command=_expand(command.strip(), environ, path),
            args=[_expand(item, environ, path) for item in args],
            env={key: _expand(value, environ, path) for key, value in env.items()},
            source_path=path,
        )

    url = raw.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ConfigError(f"invalid MCP server {name!r} in {path}: url is required")
    headers = raw.get("headers", {})
    if headers is None:
        headers = {}
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
    ):
        raise ConfigError(f"invalid MCP server {name!r} in {path}: headers must be a string mapping")
    return McpServerConfig(
        name=name,
        transport="http",
        url=_expand(url.strip(), environ, path),
        headers={key: _expand(value, environ, path) for key, value in headers.items()},
        source_path=path,
    )


def _expand(value: str, environ: dict[str, str], path: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environ:
            raise ConfigError(f"missing environment variable {name!r} referenced by MCP config {path}")
        return environ[name]

    return ENV_VAR_RE.sub(replace, value)
