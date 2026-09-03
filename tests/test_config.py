from pathlib import Path

import pytest

from monkeycode.config import load_config
from monkeycode.errors import ConfigError, UnsupportedProtocolError


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_valid_config_and_masks_api_key(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: sk-secret-value
options:
  temperature: 0.2
  thinking:
    type: adaptive
    effort: medium
""",
    )

    config = load_config(path)

    assert config.protocol == "openai"
    assert config.model == "gpt-test"
    assert config.base_url == "https://example.test/v1"
    assert config.api_key.get_secret_value() == "sk-secret-value"
    assert config.options["temperature"] == 0.2
    assert config.options["thinking"]["type"] == "adaptive"
    assert "sk-secret-value" not in str(config)
    assert "***" in str(config)
    assert config.context.context_window_tokens == 32000
    assert config.context.archive_dir == ".monkeycode/context"


def test_loads_api_key_from_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MONKEYCODE_API_KEY", "sk-from-environment")
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: ${MONKEYCODE_API_KEY}
""",
    )

    config = load_config(path)

    assert config.api_key.get_secret_value() == "sk-from-environment"
    assert "sk-from-environment" not in str(config)


def test_loads_api_key_from_dotenv_next_to_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MONKEYCODE_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "MONKEYCODE_API_KEY=sk-from-dotenv\n",
        encoding="utf-8",
    )
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: ${MONKEYCODE_API_KEY}
""",
    )

    config = load_config(path)

    assert config.api_key.get_secret_value() == "sk-from-dotenv"


def test_dotenv_takes_precedence_over_stale_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MONKEYCODE_API_KEY", "sk-from-environment")
    (tmp_path / ".env").write_text(
        "MONKEYCODE_API_KEY=sk-from-dotenv\n",
        encoding="utf-8",
    )
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: ${MONKEYCODE_API_KEY}
""",
    )

    config = load_config(path)

    assert config.api_key.get_secret_value() == "sk-from-dotenv"


def test_rejects_missing_api_key_environment_variable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MONKEYCODE_MISSING_API_KEY", raising=False)
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: ${MONKEYCODE_MISSING_API_KEY}
""",
    )

    with pytest.raises(ConfigError, match="MONKEYCODE_MISSING_API_KEY"):
        load_config(path)


def test_loads_context_config_overrides(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: sk-secret-value
context:
  enabled: false
  context_window_tokens: 64000
  auto_safety_margin_tokens: 8000
  manual_safety_margin_tokens: 1000
  recent_tail_tokens: 12000
  recent_tail_min_messages: 7
  single_tool_result_tokens: 4000
  turn_tool_results_tokens: 9000
  archive_dir: ".monkeycode/custom-context"
""",
    )

    config = load_config(path)

    assert config.context.enabled is False
    assert config.context.context_window_tokens == 64000
    assert config.context.auto_safety_margin_tokens == 8000
    assert config.context.manual_safety_margin_tokens == 1000
    assert config.context.recent_tail_tokens == 12000
    assert config.context.recent_tail_min_messages == 7
    assert config.context.single_tool_result_tokens == 4000
    assert config.context.turn_tool_results_tokens == 9000
    assert config.context.archive_dir == ".monkeycode/custom-context"


def test_loads_subagent_background_flag(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: sk-secret-value
enableSubAgentBackground: false
""",
    )

    config = load_config(path)

    assert config.enable_subagent_background is False
    assert config.effective_enable_subagent_background() is False


def test_subagent_background_defaults_to_enabled(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: sk-secret-value
""",
    )

    assert load_config(path).effective_enable_subagent_background() is True


def test_loads_worktree_config(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: sk-secret-value
worktree:
  enabled: true
  root: .monkeycode/worktrees
  ttl_hours: 48
  copy_paths: [local.cfg]
  link_dirs: [node_modules]
  include_ignored: ["runtime/*.json"]
  hooks_path: .githooks
""",
    )

    config = load_config(path).worktree

    assert config.ttl_hours == 48
    assert config.copy_paths == ("local.cfg",)
    assert config.link_dirs == ("node_modules",)
    assert config.include_ignored == ("runtime/*.json",)
    assert config.hooks_path == ".githooks"


@pytest.mark.parametrize("root", ["../outside", "C:/outside", "C:relative"])
def test_rejects_unsafe_worktree_config_root(tmp_path: Path, root: str) -> None:
    path = write_config(
        tmp_path,
        f"""
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: sk-secret-value
worktree:
  root: {root}
""",
    )

    with pytest.raises(ConfigError, match="worktree.root"):
        load_config(path)


def test_rejects_invalid_context_config(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
protocol: openai
model: gpt-test
base_url: https://example.test/v1
api_key: sk-secret-value
context:
  context_window_tokens: 1000
  auto_safety_margin_tokens: 1000
""",
    )

    with pytest.raises(ConfigError, match="context_window_tokens"):
        load_config(path)


@pytest.mark.parametrize("field", ["protocol", "model", "base_url", "api_key"])
def test_requires_core_fields(tmp_path: Path, field: str) -> None:
    values = {
        "protocol": "openai",
        "model": "gpt-test",
        "base_url": "https://example.test/v1",
        "api_key": "sk-test",
    }
    values.pop(field)
    body = "\n".join(f"{key}: {value}" for key, value in values.items())
    path = write_config(tmp_path, body)

    with pytest.raises(ConfigError, match=field):
        load_config(path)


def test_rejects_unsupported_protocol(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
protocol: unknown
model: test
base_url: https://example.test
api_key: sk-test
""",
    )

    with pytest.raises(UnsupportedProtocolError, match="anthropic.*openai"):
        load_config(path)


def test_rejects_empty_api_key(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
protocol: openai
model: test
base_url: https://example.test
api_key: ""
""",
    )

    with pytest.raises(ConfigError, match="api_key"):
        load_config(path)


def test_missing_config_file_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")
