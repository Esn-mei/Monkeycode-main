from __future__ import annotations

from pathlib import Path

import pytest

from monkeycode.messages import ToolCall
from monkeycode.tools.executor import ToolExecutor
from monkeycode.tools.registry import ToolRegistry
from monkeycode.tools.search import FindFilesTool, SearchCodeTool


def executor(tmp_path: Path) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(FindFilesTool())
    registry.register(SearchCodeTool())
    return ToolExecutor(registry, workspace_root=tmp_path)


def call(name: str, arguments_json: str) -> ToolCall:
    return ToolCall(id="call_1", name=name, arguments_json=arguments_json)


def test_find_files_matches_glob_and_limits_results(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "b.py").write_text("print(2)", encoding="utf-8")
    (tmp_path / "c.txt").write_text("text", encoding="utf-8")

    result = executor(tmp_path).execute(
        call("find_files", '{"pattern":"*.py","max_results":1}')
    )

    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["files"][0].endswith(".py")
    assert result.output["truncated"] is True


def test_search_code_finds_text_regex_and_path_pattern(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def stream_chat():\n    pass\n", encoding="utf-8")
    (tmp_path / "src" / "b.txt").write_text("STREAM_CHAT", encoding="utf-8")

    text = executor(tmp_path).execute(
        call("search_code", '{"query":"stream_chat","path_pattern":"*.py"}')
    )
    regex = executor(tmp_path).execute(
        call("search_code", '{"query":"def .*chat","regex":true,"case_sensitive":true}')
    )

    assert text.success is True
    assert text.output["count"] == 1
    assert text.output["matches"][0]["path"] == "src/a.py"
    assert regex.success is True
    assert regex.output["count"] == 1


def test_search_code_skips_file_linked_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "search-tools-outside"
    outside.mkdir(exist_ok=True)
    secret = outside / "secret.py"
    secret.write_text("SECRET_TOKEN = 'outside'\n", encoding="utf-8")
    link = tmp_path / "linked-secret.py"
    try:
        link.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"symlink is not available in this environment: {exc}")

    tool_executor = executor(tmp_path)
    found = tool_executor.execute(call("find_files", '{"pattern":"*.py"}'))
    result = tool_executor.execute(call("search_code", '{"query":"SECRET_TOKEN"}'))

    assert found.success is True
    assert found.output["count"] == 0
    assert result.success is True
    assert result.output["count"] == 0


def test_search_tool_descriptions_explain_selection_boundaries() -> None:
    assert "file or directory name" in FindFilesTool.description
    assert "Do not use it to search file contents" in FindFilesTool.description
    assert "Use one call" in SearchCodeTool.description
    assert "Do not call find_files first" in SearchCodeTool.description
