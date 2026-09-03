from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from typing import Any


@dataclass
class JsonRpcError(Exception):
    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        if self.data is None:
            return f"JSON-RPC error {self.code}: {self.message}"
        return f"JSON-RPC error {self.code}: {self.message} ({self.data!r})"


class JsonRpcProtocol:
    def __init__(self) -> None:
        self._ids = count(1)

    def next_request(self, method: str, params: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        request_id = next(self._ids)
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return request_id, payload

    def notification(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return payload


def extract_result(message: dict[str, Any], request_id: int) -> Any:
    if message.get("jsonrpc") != "2.0":
        raise JsonRpcError(-32600, "invalid JSON-RPC response", message)
    if message.get("id") != request_id:
        raise JsonRpcError(-32000, f"response id mismatch: expected {request_id}, got {message.get('id')}")
    if "error" in message:
        error = message["error"]
        if isinstance(error, dict):
            raise JsonRpcError(
                int(error.get("code", -32000)),
                str(error.get("message", "unknown JSON-RPC error")),
                error.get("data"),
            )
        raise JsonRpcError(-32000, str(error))
    return message.get("result")
