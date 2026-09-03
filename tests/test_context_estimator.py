from monkeycode.context import TokenEstimator, estimate_text_tokens
from monkeycode.messages import ChatMessage, ToolDefinition
from monkeycode.prompting import ProviderPromptPayload


def test_estimates_request_tokens_without_usage_anchor() -> None:
    estimator = TokenEstimator()
    messages = [ChatMessage(role="user", content="hello" * 20)]
    prompt = ProviderPromptPayload(stable_system_text="system prompt")
    tools = [ToolDefinition(name="read_file", description="Read.", parameters_schema={"type": "object"})]

    tokens = estimator.estimate_request_tokens(messages, prompt, tools)

    assert tokens > estimate_text_tokens(messages[0].content)


def test_usage_anchor_is_used_for_existing_request() -> None:
    estimator = TokenEstimator()
    messages = [ChatMessage(role="user", content="hello")]
    prompt = ProviderPromptPayload(stable_system_text="system")
    request_chars = estimator.request_char_count(messages, prompt, [])

    estimator.record_usage({"prompt_tokens": 123}, request_chars=request_chars)

    assert estimator.estimate_request_tokens(messages, prompt, []) == 123


def test_new_content_after_usage_anchor_is_estimated_as_delta() -> None:
    estimator = TokenEstimator()
    messages = [ChatMessage(role="user", content="hello")]
    prompt = ProviderPromptPayload(stable_system_text="system")
    request_chars = estimator.request_char_count(messages, prompt, [])
    estimator.record_usage({"input_tokens": 50}, request_chars=request_chars)

    longer = [*messages, ChatMessage(role="assistant", content="x" * 80)]

    assert estimator.estimate_request_tokens(longer, prompt, []) > 50
