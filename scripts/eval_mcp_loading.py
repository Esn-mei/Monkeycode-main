#!/usr/bin/env python3
"""Benchmark eager MCP tool loading against lazy first-use loading."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from monkeycode.mcp.client import McpSession
from monkeycode.mcp.config import load_mcp_config


def eager_load(config, workspace: Path) -> tuple[float, int, list[str]]:
    started = time.perf_counter()
    total = 0
    names: list[str] = []
    sessions: list[McpSession] = []
    try:
        for server_name, server in config.servers.items():
            session = McpSession(server, workspace_root=workspace)
            session.initialize()
            tools = session.list_tools()
            sessions.append(session)
            total += len(tools)
            names.extend(f"{server_name}::{tool.name}" for tool in tools)
    finally:
        for session in sessions:
            session.close()
    return (time.perf_counter() - started) * 1000, total, names


def lazy_first_use(config, workspace: Path, server_name: str | None) -> tuple[float, float, int, list[str]]:
    startup_started = time.perf_counter()
    # Lazy startup only parses configuration. No MCP process or HTTP session
    # is initialized until a tool on a selected server is requested.
    available = list(config.servers)
    startup_ms = (time.perf_counter() - startup_started) * 1000
    selected = server_name or (available[0] if available else None)
    if selected is None or selected not in config.servers:
        return startup_ms, 0.0, 0, []
    first_started = time.perf_counter()
    session = McpSession(config.servers[selected], workspace_root=workspace)
    try:
        session.initialize()
        tools = session.list_tools()
        first_use_ms = (time.perf_counter() - first_started) * 1000
        return startup_ms, first_use_ms, len(tools), [f"{selected}::{tool.name}" for tool in tools]
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=None, help="MCP YAML；默认读取 workspace/monkeycode.mcp.yaml")
    parser.add_argument("--server", default=None, help="延迟模式首次使用的服务器名")
    args = parser.parse_args()
    config = load_mcp_config(args.workspace, project_path=args.config)
    eager_ms, eager_count, eager_names = eager_load(config, args.workspace)
    lazy_start_ms, lazy_first_ms, lazy_count, lazy_names = lazy_first_use(config, args.workspace, args.server)
    result = {
        "servers": list(config.servers),
        "eager": {"startup_ms": round(eager_ms, 2), "tools_loaded": eager_count, "tool_names": eager_names},
        "lazy": {"startup_ms": round(lazy_start_ms, 2), "first_use_ms": round(lazy_first_ms, 2), "tools_loaded_on_first_use": lazy_count, "tool_names": lazy_names},
        "startup_speedup": round(eager_ms / lazy_start_ms, 2) if lazy_start_ms else None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
