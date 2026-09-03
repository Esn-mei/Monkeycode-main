"""Deliberately simple context manager used as an A/B evaluation baseline.

This module is kept separate from ``monkeycode.context``. It intentionally
omits recent-tail preservation, tool-result archiving, boundary messages,
failure breaker logic, and usage-anchored estimation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ContextConfig
from .context import ContextStatus, TokenEstimator, _stream_provider_without_tools, _summary_prompt
from .messages import ChatMessage
from .prompting import ProviderPromptPayload
from .session import ChatSession


class BaselineContextManager:
    """Naive all-history summarization baseline for retention experiments."""

    def __init__(self, provider: Any, workspace_root: Path, config: ContextConfig | None = None) -> None:
        self.provider = provider
        self.workspace_root = workspace_root
        self.config = config or ContextConfig()
        self.estimator = TokenEstimator()
        self.last_status = ContextStatus(enabled=self.config.enabled)

    def prepare_before_request(
        self,
        session: ChatSession,
        *,
        prompt_payload: ProviderPromptPayload | None = None,
        tools=None,
    ) -> ContextStatus:
        estimated = self.estimator.estimate_messages_tokens(session.messages)
        limit = self.config.context_window_tokens - self.config.auto_safety_margin_tokens
        if not self.config.enabled or estimated <= limit:
            return ContextStatus(enabled=self.config.enabled, estimated_tokens=estimated,
                                 safety_margin_tokens=self.config.auto_safety_margin_tokens,
                                 skipped_reason="within_budget")
        try:
            summary_parts: list[str] = []
            prompt = [ChatMessage(role="user", content=_summary_prompt(session.messages))]
            for event in _stream_provider_without_tools(
                self.provider,
                prompt,
                ProviderPromptPayload(stable_system_text="Summarize the conversation briefly."),
            ):
                if event.type == "text_delta" and event.text:
                    summary_parts.append(event.text)
            summary = "".join(summary_parts).strip()
            if not summary:
                return ContextStatus(enabled=self.config.enabled, estimated_tokens=estimated,
                                     safety_margin_tokens=self.config.auto_safety_margin_tokens,
                                     skipped_reason="empty_summary")
            session.replace_messages([ChatMessage(role="user", content=summary)])
            new_estimated = self.estimator.estimate_messages_tokens(session.messages)
            return ContextStatus(enabled=self.config.enabled, archived_count=0,
                                 summary_attempted=True, summary_created=True,
                                 estimated_tokens=new_estimated,
                                 safety_margin_tokens=self.config.auto_safety_margin_tokens)
        except Exception as exc:
            return ContextStatus(enabled=self.config.enabled, summary_attempted=True,
                                 estimated_tokens=estimated,
                                 safety_margin_tokens=self.config.auto_safety_margin_tokens,
                                 skipped_reason="summary_failed",
                                 error_message=f"{exc.__class__.__name__}: {exc}")

