from __future__ import annotations

import copy

from monkeycode.messages import ChatMessage

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"
FORK_BOILERPLATE = """<fork_boilerplate>
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则(不可协商):
1. 不能再 Fork(调用 Agent 工具会被拦截)。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具完成被分配的任务。
4. 严格限制在你被分配的任务范围内。
5. 最终报告以 "Scope:" 开头,500 字以内。
</fork_boilerplate>

"""


def build_forked_messages(parent_msgs: list[ChatMessage], task: str) -> list[ChatMessage]:
    cloned = copy.deepcopy(parent_msgs)
    pending = _pending_tool_call_ids(cloned)
    for tool_call_id in pending:
        cloned.append(
            ChatMessage(
                role="tool",
                content='{"tool_name":"fork","success":false,"error_message":"[forked, skipped]"}',
                tool_call_id=tool_call_id,
            )
        )
    cloned.append(ChatMessage(role="user", content=FORK_BOILERPLATE + task))
    return cloned


def is_fork_context(messages: list[ChatMessage]) -> bool:
    for message in messages:
        content = message.content
        if isinstance(content, str) and FORK_BOILERPLATE_TAG in content:
            return True
        if isinstance(content, list) and FORK_BOILERPLATE_TAG in str(content):
            return True
    return False


def _pending_tool_call_ids(messages: list[ChatMessage]) -> list[str]:
    pending: list[str] = []
    seen_results = {message.tool_call_id for message in messages if message.role == "tool"}
    for message in messages:
        if message.role != "assistant" or not message.tool_calls:
            continue
        for call in message.tool_calls:
            if call.id not in seen_results:
                pending.append(call.id)
    return pending
