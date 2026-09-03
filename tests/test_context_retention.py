from pathlib import Path

from monkeycode.config import ContextConfig
from monkeycode.context import ContextManager
from monkeycode.messages import StreamEvent
from monkeycode.session import ChatSession


class DeterministicSummaryProvider:
    """A predictable stand-in for the model used by the context compactor."""

    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.calls: list[list] = []

    def stream_chat(self, messages, tools=None, *, allow_tool_calls=True, prompt_payload=None):
        self.calls.append(list(messages))
        yield StreamEvent(type="text_delta", text=self.summary)
        yield StreamEvent(type="done")


def test_context_compaction_retains_all_key_facts(tmp_path: Path) -> None:
    key_facts = [
        "任务目标：修复登录超时问题",
        "关键文件：auth/service.py",
        "根本原因：refresh token 没有轮换",
        "限制条件：不能修改公开 API",
        "已完成事项：补充了登录失败测试",
        "未完成事项：还需要测试并发刷新",
    ]
    summary = "\n".join(
        [
            "## 当前目标",
            key_facts[0],
            "## 已完成事实",
            key_facts[4],
            "## 关键决策",
            key_facts[2],
            "## 未完成事项",
            key_facts[5],
            "## 重要文件/路径",
            key_facts[1],
            "## 风险",
            key_facts[3],
        ]
    )
    provider = DeterministicSummaryProvider(summary)
    session = ChatSession()
    session.add_user_message("\n".join(key_facts))
    for index in range(8):
        session.add_assistant_message(f"无关历史 {index}: " + "x" * 160)
        session.add_user_message(f"无关补充 {index}: " + "y" * 160)

    manager = ContextManager(
        provider,
        tmp_path,
        ContextConfig(
            context_window_tokens=180,
            auto_safety_margin_tokens=20,
            recent_tail_tokens=30,
            recent_tail_min_messages=3,
        ),
    )

    status = manager.prepare_before_request(session)
    compacted_text = "\n".join(
        message.content for message in session.messages if isinstance(message.content, str)
    )
    retained_facts = [fact for fact in key_facts if fact in compacted_text]
    retention_rate = len(retained_facts) / len(key_facts)

    print(
        f"context retention: {len(retained_facts)}/{len(key_facts)} "
        f"({retention_rate:.0%})"
    )
    assert status.summary_created is True
    assert provider.calls
    assert retention_rate == 1.0
    assert len(retained_facts) == len(key_facts)
