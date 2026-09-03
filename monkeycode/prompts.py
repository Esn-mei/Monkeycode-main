from __future__ import annotations

from monkeycode.events import AgentMode
from monkeycode.prompting import PromptBuilder, PromptContext

READY_HINT = "已就绪，输入 /help 查看可用命令。"
EXECUTE_DIRECTIVE = "用户已确认计划，请按刚才的计划执行。"
REVIEW_DIRECTIVE = "请审查当前上下文中的代码变更/已读取的文件，指出潜在 bug、可读性问题和可简化处。"


SYSTEM_PROMPT = PromptBuilder().build(
    PromptContext(
        workspace_root=".",
        cwd=".",
        mode=AgentMode.EXECUTE,
    )
).stable_system_text
