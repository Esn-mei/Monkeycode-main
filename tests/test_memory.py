import json
from pathlib import Path

from monkeycode.memory import MEMORY_CATEGORIES, MemoryStore, TurnSnapshot
from monkeycode.messages import ChatMessage, StreamEvent


class MemoryProvider:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "allow_tool_calls": allow_tool_calls,
                "prompt_payload": prompt_payload,
            }
        )
        yield StreamEvent(type="text_delta", text=json.dumps(self.payload, ensure_ascii=False))
        yield StreamEvent(type="done")


def test_writes_all_memory_categories_with_frontmatter(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace", tmp_path / "home")

    for category in MEMORY_CATEGORIES:
        note = store.write_note(
            scope="project",
            category=category,
            content=f"{category} content",
            source_session="session-1",
        )
        assert note is not None
        path = store.project_root / category / f"{note.id}.md"
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert f"category: {category}" in text
        assert "source_session" in text


def test_user_and_project_memory_are_separate(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace", tmp_path / "home")
    user_note = store.write_note(scope="user", category="preference", content="user", source_session="s")
    project_note = store.write_note(scope="project", category="preference", content="project", source_session="s")

    assert user_note is not None
    assert project_note is not None
    assert (store.user_root / "preference" / f"{user_note.id}.md").exists()
    assert (store.project_root / "preference" / f"{project_note.id}.md").exists()


def test_rebuild_index_is_limited(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace", tmp_path / "home")
    for index in range(260):
        store.write_note(
            scope="project",
            category="project_knowledge",
            content=f"note {index} " + "x" * 200,
            source_session="s",
        )

    index_text = store.load_index("project")

    assert len(index_text.splitlines()) <= 200
    assert len(index_text.encode("utf-8")) <= 25_000


def test_sensitive_content_is_redacted(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace", tmp_path / "home")
    note = store.write_note(
        scope="project",
        category="reference",
        content="api_key: sk-secret-value",
        source_session="s",
    )

    assert note is not None
    assert "sk-secret-value" not in note.content
    assert "[redacted]" in note.content


def test_provider_update_adds_note_without_tools(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace", tmp_path / "home")
    provider = MemoryProvider(
        [
            {
                "action": "add",
                "scope": "user",
                "category": "preference",
                "content": "用户喜欢中文回答",
                "confidence": 0.9,
            }
        ]
    )
    snapshot = TurnSnapshot(session_id="s", messages=[ChatMessage(role="user", content="用中文")])

    notes = store.update_from_provider(provider, snapshot)

    assert len(notes) == 1
    assert notes[0].scope == "user"
    assert provider.calls[0]["tools"] is None
    assert provider.calls[0]["allow_tool_calls"] is False
    assert "用户喜欢中文回答" in store.load_index("user")


def test_provider_ignore_adds_nothing(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace", tmp_path / "home")
    provider = MemoryProvider([{"action": "ignore"}])

    notes = store.update_from_provider(provider, TurnSnapshot(session_id="s", messages=[]))

    assert notes == []
    assert store.load_index("project") == ""


def test_list_files_returns_sorted_markdown_files(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace", tmp_path / "home")
    (store.project_root / "project_knowledge").mkdir(parents=True)
    (store.user_root / "preference").mkdir(parents=True)
    (store.project_root / "project_knowledge" / "b.md").write_text("b", encoding="utf-8")
    (store.project_root / "project_knowledge" / "a.md").write_text("a", encoding="utf-8")
    (store.project_root / "project_knowledge" / "note.txt").write_text("x", encoding="utf-8")
    (store.project_root / "index.md").write_text("index", encoding="utf-8")
    (store.user_root / "preference" / "u.md").write_text("u", encoding="utf-8")

    project_files, user_files = store.list_files()

    assert project_files == ["a.md", "b.md", "index.md"]
    assert user_files == ["u.md"]
