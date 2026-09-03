from __future__ import annotations

from pathlib import Path

import pytest

from monkeycode.errors import ConfigError
from monkeycode.mcp.config import load_mcp_config


def test_mcp_config_merges_user_and_project_with_project_override(tmp_path: Path) -> None:
    user_config = tmp_path / "user.yaml"
    project_config = tmp_path / "project.yaml"
    user_config.write_text(
        """
mcp_servers:
  docs:
    transport: http
    url: "https://user.example/mcp"
  fs:
    transport: stdio
    command: "python"
    args: ["server.py"]
""",
        encoding="utf-8",
    )
    project_config.write_text(
        """
mcp_servers:
  docs:
    transport: http
    url: "https://project.example/mcp"
""",
        encoding="utf-8",
    )

    config = load_mcp_config(tmp_path, user_path=user_config, project_path=project_config)

    assert set(config.servers) == {"docs", "fs"}
    assert config.servers["docs"].url == "https://project.example/mcp"
    assert config.servers["fs"].command == "python"


def test_mcp_config_expands_environment_values(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        """
mcp_servers:
  fs:
    transport: stdio
    command: "${PYTHON_BIN}"
    args: ["${PROJECT_DIR}"]
    env:
      TOKEN: "${TOKEN}"
  docs:
    transport: http
    url: "https://${HOST}/mcp"
    headers:
      Authorization: "Bearer ${TOKEN}"
""",
        encoding="utf-8",
    )

    config = load_mcp_config(
        tmp_path,
        user_path=tmp_path / "missing.yaml",
        project_path=config_path,
        environ={
            "PYTHON_BIN": "python",
            "PROJECT_DIR": "C:/repo",
            "TOKEN": "secret",
            "HOST": "example.test",
        },
    )

    assert config.servers["fs"].command == "python"
    assert config.servers["fs"].args == ["C:/repo"]
    assert config.servers["fs"].env == {"TOKEN": "secret"}
    assert config.servers["docs"].url == "https://example.test/mcp"
    assert config.servers["docs"].headers["Authorization"] == "Bearer secret"


def test_mcp_config_missing_environment_variable_is_config_error(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        """
mcp_servers:
  docs:
    transport: http
    url: "https://${MISSING}/mcp"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="MISSING"):
        load_mcp_config(
            tmp_path,
            user_path=tmp_path / "missing.yaml",
            project_path=config_path,
            environ={},
        )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("mcp_servers:\n  docs:\n    transport: http\n", "url is required"),
        ("mcp_servers:\n  fs:\n    transport: stdio\n", "command is required"),
        ("mcp_servers:\n  x:\n    transport: websocket\n", "transport must be stdio or http"),
    ],
)
def test_mcp_config_validates_required_fields(tmp_path: Path, body: str, message: str) -> None:
    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(body, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_mcp_config(tmp_path, user_path=tmp_path / "missing.yaml", project_path=config_path)
