from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from monkeycode.mcp.config import McpServerConfig

MCP_PROTOCOL_VERSION = "2025-06-18"


class McpTransportError(Exception):
    pass


class McpTransport(ABC):
    @abstractmethod
    def request(self, payload: dict[str, Any], request_id: int, timeout_seconds: float) -> dict[str, Any]:
        ...

    @abstractmethod
    def notify(self, payload: dict[str, Any], timeout_seconds: float) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...


class StdioTransport(McpTransport):
    def __init__(self, config: McpServerConfig, *, cwd: Path) -> None:
        self.config = config
        self.cwd = cwd
        self.process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: list[str] = []
        self._lock = threading.Lock()

    def request(self, payload: dict[str, Any], request_id: int, timeout_seconds: float) -> dict[str, Any]:
        with self._lock:
            self._ensure_started()
            self._write(payload)
            return self._read_response(request_id, timeout_seconds)

    def notify(self, payload: dict[str, Any], timeout_seconds: float) -> None:
        with self._lock:
            self._ensure_started()
            self._write(payload)

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        self.process = None

    @property
    def diagnostics(self) -> str:
        return "\n".join(self._stderr[-20:])

    def _ensure_started(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if not self.config.command:
            raise McpTransportError("stdio MCP server command is missing")
        executable = shutil.which(self.config.command)
        if executable is None:
            raise McpTransportError(
                f"stdio MCP server {self.config.name}: command {self.config.command!r} not found"
            )
        env = os.environ.copy()
        env.update(self.config.env)
        try:
            self.process = subprocess.Popen(
                [executable, *self.config.args],
                cwd=str(self.cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise McpTransportError(f"failed to start stdio MCP server {self.config.name}: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _write(self, payload: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise McpTransportError(f"stdio MCP server {self.config.name} is not running")
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise McpTransportError(f"failed to write to stdio MCP server {self.config.name}: {exc}") from exc

    def _read_response(self, request_id: int, timeout_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = f": {self.diagnostics}" if self.diagnostics else ""
                raise McpTransportError(
                    f"timed out waiting for MCP response from {self.config.name}{detail}"
                )
            process = self.process
            if process is not None and process.poll() is not None and self._messages.empty():
                detail = f": {self.diagnostics}" if self.diagnostics else ""
                raise McpTransportError(f"stdio MCP server {self.config.name} exited{detail}")
            try:
                message = self._messages.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if message.get("id") == request_id:
                return message

    def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                self._stderr.append(f"invalid JSON on stdout: {text}")
                continue
            if isinstance(message, dict):
                self._messages.put(message)

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr.append(line.rstrip())


class HttpTransport(McpTransport):
    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self.session_id: str | None = None

    def request(self, payload: dict[str, Any], request_id: int, timeout_seconds: float) -> dict[str, Any]:
        response, headers = self._post(payload, timeout_seconds)
        session_id = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id
        messages = _parse_http_response(response, headers.get("Content-Type", ""))
        for message in messages:
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        raise McpTransportError(f"HTTP MCP server {self.config.name} did not return response id {request_id}")

    def notify(self, payload: dict[str, Any], timeout_seconds: float) -> None:
        self._post(payload, timeout_seconds)

    def close(self) -> None:
        self.session_id = None

    def _post(self, payload: dict[str, Any], timeout_seconds: float) -> tuple[str, dict[str, str]]:
        if not self.config.url:
            raise McpTransportError("HTTP MCP server url is missing")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            **self.config.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urlrequest.Request(self.config.url, data=body, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(req, timeout=timeout_seconds) as response:
                text = response.read().decode("utf-8", errors="replace")
                response_headers = {key: value for key, value in response.headers.items()}
                return text, response_headers
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise McpTransportError(f"HTTP MCP server {self.config.name} returned {exc.code}: {detail}") from exc
        except OSError as exc:
            raise McpTransportError(f"failed to reach HTTP MCP server {self.config.name}: {exc}") from exc


def create_transport(config: McpServerConfig, *, cwd: Path) -> McpTransport:
    if config.transport == "stdio":
        return StdioTransport(config, cwd=cwd)
    return HttpTransport(config)


def _parse_http_response(text: str, content_type: str) -> list[Any]:
    if "text/event-stream" in content_type.lower():
        messages: list[Any] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            data = stripped[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                messages.append(json.loads(data))
            except json.JSONDecodeError as exc:
                raise McpTransportError(f"invalid SSE JSON-RPC data: {data}") from exc
        return messages
    try:
        parsed = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise McpTransportError(f"invalid HTTP JSON-RPC response: {text}") from exc
    if isinstance(parsed, list):
        return parsed
    return [parsed]
