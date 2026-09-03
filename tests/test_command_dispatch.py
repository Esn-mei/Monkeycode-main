import pytest

from monkeycode.command import parse


@pytest.mark.parametrize(
    ("input_text", "expected"),
    [
        ("", ("", False)),
        (" ", ("", False)),
        ("hello", ("", False)),
        ("/", ("", True)),
        ("/help", ("help", True)),
        (" /HELP ", ("help", True)),
        ("/help xx", ("", True)),
        ("/help ", ("help", True)),
        ("//double", ("/double", True)),
        ("/ /help", ("", True)),
    ],
)
def test_parse(input_text: str, expected: tuple[str, bool]) -> None:
    assert parse(input_text) == expected
