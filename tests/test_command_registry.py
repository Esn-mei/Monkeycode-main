import pytest

from monkeycode.command import Command, Kind, Registry


async def noop(_ui) -> None:
    return None


def command(name: str, *, aliases: list[str] | None = None, hidden: bool = False) -> Command:
    return Command(name, f"{name} desc", Kind.LOCAL, noop, aliases=aliases or [], hidden=hidden)


def test_register_ok_and_lookup_alias() -> None:
    registry = Registry()
    cmd = command("help", aliases=["h"])

    registry.register(cmd)

    assert registry.lookup("HELP") is cmd
    assert registry.lookup("h") is cmd


def test_register_duplicate_name_raises() -> None:
    registry = Registry()
    registry.register(command("help"))

    with pytest.raises(RuntimeError, match="command conflict: help"):
        registry.register(command("help"))


def test_register_duplicate_alias_raises() -> None:
    registry = Registry()
    registry.register(command("help", aliases=["h"]))

    with pytest.raises(RuntimeError, match="command conflict: h"):
        registry.register(command("hint", aliases=["h"]))


def test_visible_sorted_and_hidden_excluded() -> None:
    registry = Registry()
    registry.register(command("status"))
    registry.register(command("help"))
    registry.register(command("secret", hidden=True))

    assert [cmd.name for cmd in registry.visible()] == ["help", "status"]


def test_prefix_match() -> None:
    registry = Registry()
    for name in ["status", "session", "help"]:
        registry.register(command(name))

    assert [cmd.name for cmd in registry.prefix_match("/s")] == ["session", "status"]
    assert [cmd.name for cmd in registry.prefix_match("/")] == ["help", "session", "status"]
