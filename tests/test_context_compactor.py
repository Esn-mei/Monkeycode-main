from pathlib import Path

from monkeycode.config import ContextConfig
from monkeycode.context import BOUNDARY_MESSAGE, ContextManager
from monkeycode.messages import ChatMessage, StreamEvent
from monkeycode.session import ChatSession


class SummaryProvider:
    def __init__(self, summary: str | None = None, fail: bool = False) -> None:
        self.summary = summary or "\n".join(
            [
                "## 当前目标",
                "压缩上下文",
                "## 已完成事实",
                "已有历史",
                "## 关键决策",
                "保留尾部",
                "## 未完成事项",
                "继续执行",
                "## 重要文件/路径",
                "spec.md",
                "## 工具结果归档索引",
                "无",
                "## 风险",
                "估算误差",
                "## 下一步建议",
                "继续",
            ]
        )
        self.fail = fail
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
        if self.fail:
            raise RuntimeError("summary exploded")
        yield StreamEvent(type="text_delta", text=self.summary)
        yield StreamEvent(type="done")


def _long_session(count: int = 8) -> ChatSession:
    session = ChatSession()
    for index in range(count):
        session.add_user_message(f"user {index} " + "x" * 120)
        session.add_assistant_message(f"assistant {index} " + "y" * 120)
    return session


def test_compacts_old_messages_and_keeps_recent_tail(tmp_path: Path) -> None:
    provider = SummaryProvider()
    session = _long_session()
    manager = ContextManager(
        provider,
        tmp_path,
        ContextConfig(
            context_window_tokens=120,
            auto_safety_margin_tokens=10,
            recent_tail_tokens=20,
            recent_tail_min_messages=3,
        ),
    )

    status = manager.prepare_before_request(session)

    assert status.summary_created is True
    assert provider.calls[0]["tools"] is None
    assert provider.calls[0]["allow_tool_calls"] is False
    assert "绝对禁止调用任何工具" in provider.calls[0]["messages"][0].content
    assert "当前目标" in session.messages[0].content
    assert session.messages[1].content == BOUNDARY_MESSAGE
    assert len(session.messages) >= 5


def test_summary_failure_does_not_replace_history(tmp_path: Path) -> None:
    provider = SummaryProvider(fail=True)
    session = _long_session()
    before = session.messages
    manager = ContextManager(
        provider,
        tmp_path,
        ContextConfig(context_window_tokens=120, auto_safety_margin_tokens=10, recent_tail_tokens=20),
    )

    status = manager.prepare_before_request(session)

    assert status.summary_created is False
    assert status.error_message
    assert session.messages == before


def test_summary_breaker_stops_after_three_failures(tmp_path: Path) -> None:
    provider = SummaryProvider(fail=True)
    session = _long_session()
    manager = ContextManager(
        provider,
        tmp_path,
        ContextConfig(context_window_tokens=120, auto_safety_margin_tokens=10, recent_tail_tokens=20),
    )

    for _ in range(3):
        status = manager.prepare_before_request(session)

    assert status.breaker_active is True
    fourth = manager.prepare_before_request(session)
    assert fourth.skipped_reason == "summary_breaker_active"
    assert len(provider.calls) == 3
