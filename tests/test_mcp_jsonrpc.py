from __future__ import annotations

import pytest

from monkeycode.mcp.jsonrpc import JsonRpcError, JsonRpcProtocol, extract_result


def test_jsonrpc_request_ids_increment() -> None:
    protocol = JsonRpcProtocol()

    first_id, first = protocol.next_request("initialize", {})
    second_id, second = protocol.next_request("tools/list", {})

    assert first_id == 1
    assert second_id == 2
    assert first["jsonrpc"] == "2.0"
    assert second["id"] == 2


def test_jsonrpc_extracts_matching_result() -> None:
    assert extract_result({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}, 7) == {"ok": True}


def test_jsonrpc_rejects_id_mismatch() -> None:
    with pytest.raises(JsonRpcError, match="response id mismatch"):
        extract_result({"jsonrpc": "2.0", "id": 8, "result": {}}, 7)


def test_jsonrpc_error_response_becomes_exception() -> None:
    with pytest.raises(JsonRpcError, match="boom") as exc:
        extract_result(
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}},
            1,
        )

    assert exc.value.code == -32000
