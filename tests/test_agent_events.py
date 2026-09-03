from monkeycode.events import AgentConfig, AgentEvent, AgentMode, CancellationToken


def test_agent_config_defaults_match_design() -> None:
    config = AgentConfig()

    assert config.max_iterations == 10
    assert config.max_consecutive_unknown_tools == 2
    assert config.default_tool_timeout_seconds == 10.0
    assert config.max_parallel_tools == 4
    assert config.soft_tool_budget == 6
    assert config.max_output_chars == 12000


def test_agent_event_carries_minimal_fields() -> None:
    event = AgentEvent(
        type="progress",
        turn_index=1,
        iteration=2,
        progress="iteration 2",
        mode=AgentMode.EXECUTE,
    )

    assert event.type == "progress"
    assert event.turn_index == 1
    assert event.iteration == 2
    assert event.progress == "iteration 2"
    assert event.mode == AgentMode.EXECUTE


def test_cancellation_token_can_be_set() -> None:
    token = CancellationToken()

    assert token.cancelled is False
    token.cancel()
    assert token.cancelled is True
