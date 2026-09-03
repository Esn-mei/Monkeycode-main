from pathlib import Path

import pytest

from monkeycode.cli import build_parser, resolve_config_path


def test_default_config_uses_config_yaml_when_monkeycode_yaml_missing(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("protocol: openai\n", encoding="utf-8")

    assert resolve_config_path(None, cwd=tmp_path) == config


def test_default_config_prefers_monkeycode_yaml(tmp_path: Path) -> None:
    monkeycode = tmp_path / "monkeycode.yaml"
    config = tmp_path / "config.yaml"
    monkeycode.write_text("protocol: openai\n", encoding="utf-8")
    config.write_text("protocol: anthropic\n", encoding="utf-8")

    assert resolve_config_path(None, cwd=tmp_path) == monkeycode


def test_explicit_config_path_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.yaml"

    assert resolve_config_path(explicit, cwd=tmp_path) == explicit


def test_permission_mode_argument_accepts_supported_modes() -> None:
    parser = build_parser()

    assert parser.parse_args(["--permission-mode", "strict"]).permission_mode == "strict"
    assert parser.parse_args(["--permission-mode", "default"]).permission_mode == "default"
    assert parser.parse_args(["--permission-mode", "allow"]).permission_mode == "allow"
    assert parser.parse_args(["--permission-mode", "Default permissions"]).permission_mode == "default"
    assert parser.parse_args(["--permission-mode", "Auto-review"]).permission_mode == "strict"
    assert parser.parse_args(["--permission-mode", "Full access"]).permission_mode == "allow"


def test_permission_mode_defaults_to_default() -> None:
    parser = build_parser()

    assert parser.parse_args([]).permission_mode == "default"


def test_invalid_permission_mode_is_rejected() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--permission-mode", "unsafe"])
