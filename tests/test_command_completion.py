from monkeycode.command import CompletionMenu, Registry, register_builtins


def registry() -> Registry:
    reg = Registry()
    register_builtins(reg)
    return reg


def test_completion_updates_from_prefix() -> None:
    menu = CompletionMenu()

    menu.update("/s", registry())

    assert menu.active is True
    assert [item.name for item in menu.items] == ["session", "skill", "status"]


def test_completion_zero_match_renders_hint() -> None:
    menu = CompletionMenu()

    menu.update("/zzz", registry())

    assert menu.selected() is None
    assert "无匹配" in menu.render()


def test_completion_move_and_select() -> None:
    menu = CompletionMenu()
    menu.update("/s", registry())

    menu.move_down()

    assert menu.selected().name == "skill"
    menu.move_up()
    assert menu.selected().name == "session"


def test_completion_hide() -> None:
    menu = CompletionMenu()
    menu.update("/", registry())

    menu.hide()

    assert menu.active is False
    assert menu.items == []
